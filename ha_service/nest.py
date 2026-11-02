"""Conservative visit/collection tracking, independent of image capture."""
import json

from data_layout import LABEL, RESERVED
from .options import AppError
from .storage import atomic_json, safe_path


def valid_hen(value):
    return (isinstance(value, str) and
            (value == 'unknown' or (LABEL.fullmatch(value) and value not in RESERVED)))


def count(value):
    return value is None or (type(value) is int and 0 <= value <= 1000000)


class NestState:
    def __init__(self, root):
        self.path = safe_path(root, 'nest-state.json')
        self.state = {'version': 2, 'hen': None, 'eggs': None,
                      'baseline': None, 'latest_event': None, 'confirmed': False,
                      'pending_hen': None, 'pending_baseline': None}
        if self.path.exists():
            try:
                if not self.path.is_file() or self.path.stat().st_size > 4096:
                    raise ValueError()
                state = json.loads(self.path.read_text())
                legacy_keys = {'version', 'hen', 'eggs', 'baseline', 'latest_event'}
                if (isinstance(state, dict) and set(state) == legacy_keys
                        and type(state['version']) is int and state['version'] == 1):
                    state.update(version=2, confirmed=(state['hen'] is not None or
                                                      state['eggs'] is not None),
                                 pending_hen=None, pending_baseline=None)
                if (not isinstance(state, dict) or set(state) != set(self.state)
                        or type(state['version']) is not int or state['version'] != 2
                        or type(state['confirmed']) is not bool
                        or (state['hen'] is not None and not valid_hen(state['hen']))
                        or (state['pending_hen'] is not None and
                            not valid_hen(state['pending_hen']))
                        or not count(state['pending_baseline'])
                        or (state['pending_hen'] is None and state['pending_baseline'] is not None)
                        or (state['pending_hen'] is not None and state['hen'] is not None)
                        or (not state['confirmed'] and any(state[key] is not None for key in
                            ('hen', 'eggs', 'baseline', 'pending_hen', 'pending_baseline')))
                        or not count(state['eggs']) or not count(state['baseline'])
                        or (state['hen'] is None and state['baseline'] is not None)
                        or (state['latest_event'] is not None and
                            (not isinstance(state['latest_event'], str)
                             or not 1 <= len(state['latest_event']) <= 256
                             or any(ord(c) < 32 for c in state['latest_event'])))):
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

    def snapshot(self):
        """Public live fields from one committed state, never raw predictions."""
        hen = self.state['hen']
        return {'occupant': (hen.capitalize() if hen != 'unknown' else 'unknown')
                if hen is not None else ('empty' if self.state['confirmed'] else None),
                'egg_count': self.state['eggs'],
                'latest_event': self.state['latest_event']}

    def observe(self, label, eggs):
        if label != 'no_chicken' and not valid_hen(label):
            raise AppError('Invalid nest observation.')
        if label == 'no_chicken' and (not count(eggs) or eggs is None):
            raise AppError('Invalid nest egg count.')
        state = dict(self.state)
        hen = state['hen']
        # Unknown frames establish presence, but never erase a known identity.
        target = hen if label == 'unknown' and hen is not None else label
        candidate = target
        streak = min(3, self.streak + 1) if candidate == self.candidate else 1
        egg_candidate = eggs if label == 'no_chicken' else None
        egg_streak = (min(3, self.egg_streak + 1)
                      if egg_candidate is not None and egg_candidate == self.egg_candidate
                      else 1 if egg_candidate is not None else 0)
        events = []
        if streak >= 3:
            state['confirmed'] = True
            if target == 'no_chicken' and hen is not None:
                name = hen.capitalize() if hen != 'unknown' else 'Unknown'
                laid = (egg_streak >= 3 and state['baseline'] is not None
                        and eggs > state['baseline'])
                events.append(name + (' laid an Egg' if laid else ' left'))
                if egg_streak < 3:
                    state['pending_hen'] = hen
                    state['pending_baseline'] = state['baseline']
                state['hen'] = None
                state['baseline'] = None
            elif target != 'no_chicken' and target != hen:
                if hen is not None and hen != 'unknown':
                    events.append(hen.capitalize() + ' left')
                # No empty frame between hens means hidden eggs cannot be attributed.
                if hen is None:
                    # An unsettled post-visit count is not a safe new baseline.
                    state['baseline'] = (state['eggs'] if state['pending_hen'] is None
                                         else None)
                elif hen != 'unknown':
                    state['baseline'] = None
                state['hen'] = target
                state['pending_hen'] = None
                state['pending_baseline'] = None
                events.append(target.capitalize() + ' arrived')

        if label == 'no_chicken' and state['hen'] is None and egg_streak >= 3:
            if (state['pending_hen'] is not None and state['pending_baseline'] is not None
                    and eggs > state['pending_baseline']):
                events.append(state['pending_hen'].capitalize() + ' laid an Egg')
            if eggs == 0 and state['eggs'] is not None and state['eggs'] > 0:
                n = state['eggs']
                events.append('{} {} collected'.format(n, 'egg' if n == 1 else 'eggs'))
            state['eggs'] = eggs
            state['pending_hen'] = None
            state['pending_baseline'] = None
        if events:
            state['latest_event'] = events[-1]
        # Persist before changing memory or publishing. Failed writes cannot consume
        # a transition. atomic_json fsyncs both file and directory; no pickle input.
        safe_path(self.path.parent, self.path.name)
        atomic_json(self.path, state)
        self.state = state
        self.candidate, self.streak = candidate, streak
        self.egg_candidate, self.egg_streak = egg_candidate, egg_streak
        return events
