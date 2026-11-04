"""Conservative visit/collection tracking, independent of image capture."""
import json
import copy
from datetime import date, datetime

from data_layout import LABEL, RESERVED
from .options import AppError
from .storage import atomic_json, safe_path
from .journal import empty_evidence, evolve, valid_evidence

ARRIVAL_FRAMES = 3
DEPARTURE_FRAMES = 5
EGG_FRAMES = 3


def valid_hen(value):
    return (isinstance(value, str) and
            (value == 'unknown' or (LABEL.fullmatch(value) and value not in RESERVED)))


def count(value):
    return value is None or (type(value) is int and 0 <= value <= 1000000)


def valid_daily(value):
    if not isinstance(value, dict) or set(value) != {'date', 'counts'}:
        return False
    day, counts = value['date'], value['counts']
    if day is not None:
        try:
            if not isinstance(day, str) or datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                return False
        except ValueError:
            return False
    return (isinstance(counts, dict) and 'unknown' in counts and len(counts) <= 1000
            and all(valid_hen(hen) and n is not None and count(n) for hen, n in counts.items())
            and (day is not None or not any(counts.values())))


STATE_KEYS = {'version', 'hen', 'eggs', 'baseline', 'latest_event', 'confirmed',
              'pending_hen', 'pending_baseline', 'pending_settled', 'egg_high_water',
              'daily_eggs', 'journal'}


def valid_state(state):
    """Validate region snapshots too, before replaying a split transaction."""
    return (isinstance(state, dict) and set(state) == STATE_KEYS
            and type(state['version']) is int and state['version'] == 5
            and valid_evidence(state['journal']) and valid_daily(state['daily_eggs'])
            and type(state['confirmed']) is bool
            and all(state[key] is None or valid_hen(state[key]) for key in ('hen', 'pending_hen'))
            and all(count(state[key]) for key in ('eggs', 'baseline', 'pending_baseline', 'egg_high_water'))
            and type(state['pending_settled']) is bool
            and (not state['pending_settled'] or state['pending_hen'] is not None)
            and ((state['eggs'] is None and state['egg_high_water'] is None) or
                 (state['eggs'] is not None and state['egg_high_water'] is not None
                  and state['egg_high_water'] >= state['eggs']))
            and (state['pending_hen'] is not None or state['pending_baseline'] is None)
            and (state['pending_hen'] is None or state['hen'] is None)
            # Position can become uncertain after a move; retain the clean egg
            # count independently, never replace it with an invented zero.
            and (state['confirmed'] or all(state[key] is None for key in
                 ('hen', 'baseline', 'pending_hen', 'pending_baseline')))
            and (state['hen'] is not None or state['baseline'] is None)
            and (state['latest_event'] is None or
                 (isinstance(state['latest_event'], str) and 1 <= len(state['latest_event']) <= 256
                  and not any(ord(c) < 32 for c in state['latest_event']))))


class NestState:
    def __init__(self, root, today=None, zone=None, layout=None, count_initial_eggs=True):
        self.zone = zone
        self.layout = layout
        self.count_initial_eggs = count_initial_eggs
        self.today = today or date.today
        self.path = safe_path(root, 'nest-state.json')
        self.state = {'version': 5, 'hen': None, 'eggs': None,
                      'baseline': None, 'latest_event': None, 'confirmed': False,
                      'pending_hen': None, 'pending_baseline': None,
                      'pending_settled': False, 'egg_high_water': None,
                      'daily_eggs': {'date': None, 'counts': {'unknown': 0}},
                      'journal': empty_evidence()}
        if self.path.exists():
            try:
                if not self.path.is_file() or self.path.stat().st_size > 65536:
                    raise ValueError()
                state = json.loads(self.path.read_text())
                legacy_keys = {'version', 'hen', 'eggs', 'baseline', 'latest_event'}
                if (isinstance(state, dict) and set(state) == legacy_keys
                        and type(state['version']) is int and state['version'] == 1):
                    state.update(version=2, confirmed=(state['hen'] is not None or
                                                      state['eggs'] is not None),
                                 pending_hen=None, pending_baseline=None)
                new_fields = {'pending_settled', 'egg_high_water'}
                if (isinstance(state, dict) and set(state) == set(self.state) - {'daily_eggs', 'journal'} - new_fields
                        and type(state['version']) is int and state['version'] == 2):
                    state.update(version=3, daily_eggs={'date': None, 'counts': {'unknown': 0}})
                if (isinstance(state, dict) and set(state) == set(self.state) - {'journal'} - new_fields
                        and type(state['version']) is int and state['version'] == 3):
                    state.update(version=4, journal=empty_evidence())
                if (isinstance(state, dict) and set(state) == set(self.state) - new_fields
                        and type(state['version']) is int and state['version'] == 4):
                    # Do not infer a visitor from historical event text.
                    state.update(version=5, pending_settled=False,
                                 egg_high_water=state['eggs'])
                if not valid_state(state):
                    raise ValueError()
                self.state = state
            except Exception:
                raise AppError('Nest state is invalid; restore nest-state.json from backup or explicitly remove it to start with an unknown baseline.') from None
        self.reset_confirmation()

    def reset_confirmation(self):
        # Restarts and failed observations cannot provide consecutive evidence.
        self.candidate = None
        self.streak = 0
        self.egg_candidate = None
        self.egg_streak = 0
        self.state['journal']['presence'] = []
        self.state['journal']['count'] = []

    def _dated_state(self, identities=()):
        """Copy counters too: failed writes must never mutate live totals."""
        state = dict(self.state)
        daily = self.state['daily_eggs']
        day = self.today().isoformat()
        if daily['date'] is not None and day < daily['date']:
            raise AppError('Local date moved backwards; check service timezone and clock before counting eggs.')
        counts = {hen: n if day == daily['date'] else 0
                  for hen, n in daily['counts'].items()}
        for hen in identities:
            if not valid_hen(hen):
                raise AppError('Invalid daily egg identity.')
            counts.setdefault(hen, 0)
        state['daily_eggs'] = {'date': day, 'counts': counts}
        return state

    def _commit(self, state):
        if not valid_daily(state['daily_eggs']):
            raise AppError('Invalid daily egg totals; no state committed.')
        if not valid_evidence(state['journal']):
            raise AppError('Invalid journal evidence; no state committed.')
        safe_path(self.path.parent, self.path.name)
        atomic_json(self.path, state)
        self.state = state

    def update_day(self, identities=()):
        """Roll over even during scheduled capture pauses, without changing streaks."""
        state = self._dated_state(identities)
        if state != self.state:
            self._commit(state)

    def daily_snapshot(self):
        daily = self.state['daily_eggs']
        return {'date': daily['date'], 'counts': dict(daily['counts'])}

    def obscure(self, pause=False):
        """No empty observation is implied by a hidden/ambiguous nest."""
        if not pause:
            self.reset_confirmation()
        state = copy.deepcopy(self.state)
        state.update(baseline=None, pending_hen=None, pending_baseline=None, pending_settled=False)
        state['journal']['baseline'] = []
        state['journal']['pending'] = []
        for group in state['journal']['visit']:
            if group['role'] == 'Before visit / previous count':
                group['frames'] = []
        if state != self.state:
            self._commit(state)

    def snapshot(self):
        """Public live fields from one committed state, never raw predictions."""
        hen = self.state['hen']
        return {'occupant': (hen.capitalize() if hen != 'unknown' else 'unknown')
                if hen is not None else ('empty' if self.state['confirmed'] else None),
                'egg_count': self.state['eggs'],
                'latest_event': self.state['latest_event']}

    def observe(self, label, eggs, evidence=None, count_allowed=True, arrival_event=None):
        if label != 'no_chicken' and not valid_hen(label):
            raise AppError('Invalid nest observation.')
        if label == 'no_chicken' and (not count(eggs) or (eggs is None and count_allowed)):
            raise AppError('Invalid nest egg count.')
        state = self._dated_state()
        hen = state['hen']
        # Unknown frames establish presence, but never erase a known identity.
        target = hen if label == 'unknown' and hen is not None else label
        candidate = target
        streak = min(DEPARTURE_FRAMES, self.streak + 1) if candidate == self.candidate else 1
        egg_candidate = eggs if label == 'no_chicken' else None
        egg_streak = (min(EGG_FRAMES, self.egg_streak + 1)
                       if egg_candidate is not None and egg_candidate == self.egg_candidate
                       else 1 if egg_candidate is not None else 0)
        if not count_allowed and label == 'no_chicken':
            egg_candidate, egg_streak = self.egg_candidate, self.egg_streak
        events = []
        laying_hen = None
        required = DEPARTURE_FRAMES if target == 'no_chicken' and hen is not None else ARRIVAL_FRAMES
        if streak >= required:
            state['confirmed'] = True
            if target == 'no_chicken' and hen is not None:
                name = hen.capitalize() if hen != 'unknown' else 'Unknown'
                laid = (count_allowed and egg_streak >= EGG_FRAMES and state['baseline'] is not None
                        and eggs > state['baseline']
                        and eggs > (state['egg_high_water'] or 0))
                if laid:
                    laying_hen = hen
                events.append(name + (' laid an Egg' if laid else ' left'))
                # A settled but missed egg must not erase the last visitor.
                state['pending_hen'] = hen
                state['pending_baseline'] = state['baseline']
                state['pending_settled'] = False
                state['hen'] = None
                state['baseline'] = None
            elif target != 'no_chicken' and target != hen:
                if hen is not None and hen != 'unknown':
                    events.append(hen.capitalize() + ' left')
                # No empty frame between hens means hidden eggs cannot be attributed.
                if hen is None:
                    # An unsettled post-visit count is not a safe new baseline.
                    state['baseline'] = (state['eggs'] if state['pending_hen'] is None or state['pending_settled']
                                         else None)
                elif hen != 'unknown':
                    state['baseline'] = None
                state['hen'] = target
                state['daily_eggs']['counts'].setdefault(target, 0)
                state['pending_hen'] = None
                state['pending_baseline'] = None
                state['pending_settled'] = False
                if arrival_event is not None:
                    # Movement through an obscured area cannot establish a safe
                    # before-visit count for the destination nest.
                    state['baseline'] = None
                events.append(arrival_event or target.capitalize() + ' arrived')
            elif target != 'no_chicken' and arrival_event is not None:
                # Reconcile duplicate known positions persisted by an older app.
                state['baseline'] = None
                events.append(arrival_event)

        if count_allowed and label == 'no_chicken' and state['hen'] is None and egg_streak >= EGG_FRAMES:
            # A stable partial decrease can be occlusion, not collection. Rebounds
            # must not count the same eggs again; only a confirmed zero resets it.
            increase = eggs - (state['egg_high_water'] or 0)
            if self.state['eggs'] is None and not self.count_initial_eggs:
                # New split geometry establishes a baseline, not new production.
                increase = 0
            if (state['pending_hen'] is not None and state['pending_baseline'] is not None
                    and eggs > state['pending_baseline'] and increase > 0):
                laying_hen = state['pending_hen']
                event = state['pending_hen'].capitalize() + ' laid an Egg'
                if event not in events:
                    events.append(event)
            # Count every confirmed increase exactly once, even without an
            # attributable visit. First-observed eggs have no known identity.
            if increase > 0:
                identity = laying_hen or 'unknown'
                counts = state['daily_eggs']['counts']
                counts[identity] = counts.get(identity, 0) + increase
            if eggs == 0 and state['eggs'] is not None and state['eggs'] > 0:
                n = state['eggs']
                events.append('{} {} collected'.format(n, 'egg' if n == 1 else 'eggs'))
            state['eggs'] = eggs
            state['egg_high_water'] = max(eggs, state['egg_high_water'] or 0)
            if state['pending_hen'] is not None:
                state['pending_settled'] = True
            if eggs == 0 and self.state['eggs'] not in (None, 0):
                # Collection ends the attribution window. Already-empty frames
                # after a visit do not: they may simply have missed the new egg.
                state['egg_high_water'] = 0
                state['pending_hen'] = None
                state['pending_baseline'] = None
                state['pending_settled'] = False
        if events:
            state['latest_event'] = events[-1]
        if evidence is not None:
            evolve(self.state, state, evidence, streak, egg_streak, events, count_allowed=count_allowed)
            if self.zone is not None:
                for entry in state['journal']['outbox']:
                    if 'zone' not in entry:
                        entry.update(zone=self.zone, layout=self.layout,
                                     text=(entry['text'] if ' moved to ' in entry['text'] else
                                           self.zone.capitalize() + ': ' + entry['text']))
        # Persist before changing memory or publishing. Failed writes cannot consume
        # a transition. atomic_json fsyncs both file and directory; no pickle input.
        self._commit(state)
        self.candidate, self.streak = candidate, streak
        self.egg_candidate, self.egg_streak = egg_candidate, egg_streak
        return events
