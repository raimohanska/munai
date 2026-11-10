"""Independent durable side/divider decisions sharing one journal archive."""
import copy
from datetime import date
import json

from .journal import ID, valid_entry
from .nest import ARRIVAL_FRAMES, NestState, valid_daily, valid_state
from .options import AppError
from .storage import atomic_json, safe_path, sync_directory
from . import egg_tracker


class SplitNest:
    def __init__(self, root, layout, carry=None, today=None):
        self.root, self.layout = root, layout
        self.today = today or date.today
        self.path = safe_path(root, 'nest-layout.json')
        self.metadata = {'version': 1, 'layout': layout, 'latest_event': None,
                         'latest_id': None, 'carry': carry or {'date': None, 'counts': {'unknown': 0}}}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 65536:
                    raise ValueError()
                saved = json.loads(self.path.read_text())
                if (set(saved) != set(self.metadata) or type(saved['version']) is not int or saved['version'] != 1
                        or saved['layout'] != layout or not valid_daily(saved['carry'])
                        or (saved['latest_event'] is not None and
                            (not isinstance(saved['latest_event'], str) or not 1 <= len(saved['latest_event']) <= 256
                             or any(ord(c) < 32 for c in saved['latest_event'])))
                        or (saved['latest_id'] is not None and (not isinstance(saved['latest_id'], str)
                                                               or not ID.fullmatch(saved['latest_id'])))
                        or ((saved['latest_id'] is None) != (saved['latest_event'] is None))):
                    raise ValueError()
                self.metadata = saved
            except Exception:
                raise AppError('Split nest state/layout is incompatible; restore its matching configuration and backup. Never map whole-box or other-axis baselines into these nests.') from None
        else:
            atomic_json(self.path, self.metadata)
        self.sides = ('left', 'right') if layout == 'vertical' else ('top', 'bottom')
        self.transition_path = safe_path(root, 'nest-transition.json')
        self._recover_transition()
        self.regions = {}
        for zone in self.sides + ('center',):
            folder = safe_path(root, 'nests/' + layout + '/' + zone)
            folder.mkdir(parents=True, exist_ok=True)
            self.regions[zone] = NestState(folder, today=self.today, zone=zone, layout=layout,
                                          count_initial_eggs=False)
        self.blocked = set(self.regions)  # No live view exists immediately after restart.
        self._refresh_latest()

    @property
    def state(self):
        return {'latest_event': self.metadata['latest_event'],
                'journal': {'regions': {z: n.state['journal'] for z, n in self.regions.items()},
                            'outbox': [entry for n in self.regions.values()
                                       for entry in n.state['journal']['outbox']]}}

    def _refresh_latest(self):
        entries = self.state['journal']['outbox']
        if entries:
            if not all(valid_entry(e) for e in entries):
                raise AppError('Invalid split journal outbox.')
            latest = max(entries, key=lambda entry: entry['id'])
            if self.metadata['latest_id'] is None or latest['id'] > self.metadata['latest_id']:
                updated = dict(self.metadata, latest_event=latest['text'], latest_id=latest['id'])
                atomic_json(self.path, updated)
                self.metadata = updated

    def _commit(self, state):
        # Journal.flush has written every outbox entry before acknowledging any
        # region. A crash during acknowledgements replays remaining entries by ID.
        if state['journal']['outbox']:
            raise AppError('Only journal acknowledgement may commit aggregate split state.')
        self._recover_transition()
        self._refresh_latest()
        for nest in self.regions.values():
            if nest.state['journal']['outbox']:
                updated = copy.deepcopy(nest.state)
                updated['journal']['outbox'] = []
                nest._commit(updated)

    def _recover_transition(self):
        """Roll all regions forward after an interrupted multi-region commit."""
        if not self.transition_path.exists():
            return
        try:
            if self.transition_path.stat().st_size > 262144:
                raise ValueError()
            saved = json.loads(self.transition_path.read_text())
            if (not isinstance(saved, dict) or set(saved) != {'layout', 'regions'}
                    or saved['layout'] != self.layout or not isinstance(saved['regions'], dict)
                    or set(saved['regions']) != set(self.sides + ('center',))
                    or not all(valid_state(state) for state in saved['regions'].values())):
                raise ValueError()
        except Exception:
            raise AppError('Split nest transition is invalid; restore its matching backup.') from None
        for zone, state in saved['regions'].items():
            folder = safe_path(self.root, 'nests/' + self.layout + '/' + zone)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(safe_path(folder, 'nest-state.json'), state)
        if hasattr(self, 'regions'):
            for zone, state in saved['regions'].items():
                self.regions[zone].state = state
                self.regions[zone].reset_confirmation()
        self.transition_path.unlink()
        sync_directory(self.root)

    def observe_zones(self, observations, evidence=None, zone_evidence=None,
                      egg_images=None, texture_threshold=8):
        if set(observations) != set(self.regions):
            raise AppError('Split observation has incorrect zones.')
        self._recover_transition()
        # Stage the whole round: a move must never persist an arrival without
        # releasing the old position, or vice versa. Region streaks stay in RAM.
        regions = {}
        for zone, nest in self.regions.items():
            staged = copy.copy(nest)
            staged.state = copy.deepcopy(nest.state)
            staged._commit = lambda state, target=staged: setattr(target, 'state', state)
            regions[zone] = staged
        observations = copy.deepcopy(observations)
        known = {}
        for zone, item in observations.items():
            label = item['label']
            nest = regions[zone]
            # Match NestState's tentative arrival identity through unknown
            # presence, so its eventual confirmation still releases an origin.
            if (label == 'unknown' and nest.state['hen'] in (None, 'unknown')
                    and nest.candidate not in (None, 'unknown', 'no_chicken')):
                label = nest.candidate
            if not item.get('obscured') and label not in ('unknown', 'no_chicken'):
                known.setdefault(label, []).append(zone)
        for zones in known.values():
            if len(zones) > 1:
                # Two independent detections with the same identity are not a
                # movement. Do not choose the larger hen or invent a new label.
                for zone in zones:
                    observations[zone].update(label='unknown', obscured=True,
                                              obscured_reason='duplicate_identity')
        moves = {}
        releases = set()
        for hen, zones in known.items():
            if len(zones) != 1:
                continue
            zone = zones[0]
            nest = regions[zone]
            streak = nest.streak + 1 if nest.candidate == hen else 1
            origins = [z for z, n in self.regions.items() if z != zone and n.state['hen'] == hen]
            # A fresh confirmation also repairs duplicate states left by older
            # releases, without fabricating a historical departure or egg.
            if origins and streak >= ARRIVAL_FRAMES:
                moves[zone] = hen.capitalize() + ' moved to ' + zone
                releases.update(origins)
        for origin in releases:
            source = regions[origin]
            source.state.update(hen=None, confirmed=False, baseline=None,
                                pending_hen=None, pending_baseline=None, pending_settled=False)
            for key in ('visit', 'pending', 'baseline'):
                source.state['journal'][key] = []
            # Preserve a different hen's two arrival shots during a simultaneous
            # swap, but discard the departing hen's presence/count candidates.
            if origin not in moves:
                source.reset_confirmation()
            else:
                source.egg_candidate, source.egg_streak = None, 0
                source.state['journal']['count'] = []
        events = []
        blocked = set()
        center = observations['center']
        center_clear = not center.get('obscured') and center['label'] == 'no_chicken'
        for zone, nest in regions.items():
            item = observations[zone]
            if item.get('obscured'):
                blocked.add(zone)
                nest.state['egg_tracking'] = egg_tracker.pause(nest.state.get('egg_tracking'))
                # Small edge/body fragments are skipped completely. Genuine
                # hidden/ambiguous nests still invalidate attribution, without
                # consuming or erasing a confirmation shot or the clean count.
                if item.get('obscured_reason') != 'small_body':
                    nest.obscure(pause=True)
                continue
            # Only the observed side and divider gate its count. A separate hen
            # in the other nest does not hide this nest. Center is presence-only.
            count_allowed = (zone in self.sides and center_clear and item['label'] == 'no_chicken')
            eggs = item['egg_count'] if count_allowed else None
            source_frame = zone_evidence.get(zone) if zone_evidence is not None else evidence
            frame = None if source_frame is None else dict(source_frame, zone=zone, label=item['label'],
                         egg_count=eggs if item['label'] == 'no_chicken' else None)
            for event in nest.observe(item['label'], eggs, frame, count_allowed=count_allowed,
                                      arrival_event=moves.get(zone),
                                      egg_image=(egg_images or {}).get(zone),
                                      egg_detections=item.get('egg_detections'),
                                      texture_threshold=texture_threshold):
                events.append(event if ' moved to ' in event else zone.capitalize() + ': ' + event)
        states = {zone: nest.state for zone, nest in regions.items()}
        if not all(valid_state(state) for state in states.values()):
            raise AppError('Invalid split nest decision; no state committed.')
        atomic_json(self.transition_path, {'layout': self.layout, 'regions': states})
        self._recover_transition()
        # Restore the staged streaks; replay on restart deliberately resets them.
        for nest in regions.values():
            del nest._commit
        self.regions, self.blocked = regions, blocked
        self._refresh_latest()
        return events

    def reset_confirmation(self):
        self._recover_transition()
        for nest in self.regions.values():
            nest.obscure()
        self.blocked = set(self.regions)

    def update_day(self, identities=()):
        self._recover_transition()
        for nest in self.regions.values():
            nest.update_day(identities)

    def daily_snapshot(self):
        day = self.today().isoformat()
        carry = self.metadata['carry']
        if carry['date'] is not None and carry['date'] > day:
            raise AppError('Local date moved backwards; check the service clock.')
        counts = {hen: n if carry['date'] == day else 0 for hen, n in carry['counts'].items()}
        for zone in self.sides:  # Divider visits never produce eggs.
            daily = self.regions[zone].daily_snapshot()
            for hen, n in daily['counts'].items():
                counts[hen] = counts.get(hen, 0) + (n if daily['date'] == day else 0)
        return {'date': day, 'counts': counts}

    def snapshot(self):
        zones = {}
        for zone, nest in self.regions.items():
            zones[zone] = dict(nest.snapshot(), obscured=zone in self.blocked)
            if zone == 'center':
                zones[zone]['egg_count'] = None  # Hide legacy placeholder zeros too.
            if zone in self.blocked:
                zones[zone]['occupant'] = 'obscured'
        counts = [zones[z]['egg_count'] for z in self.sides]
        occupants = ['{}: {}'.format(z.capitalize(), s['occupant'] or 'unknown')
                     for z, s in zones.items() if s['occupant'] != 'empty']
        return {'occupant': '; '.join(occupants) if occupants else 'empty',
                'egg_count': sum(counts) if all(n is not None for n in counts) else None,
                'latest_event': self.metadata['latest_event'], 'zones': zones, 'layout': self.layout}
