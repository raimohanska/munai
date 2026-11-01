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
        self.state = {'version': 1, 'hen': None, 'eggs': None,
                      'baseline': None, 'latest_event': None}
        if self.path.exists():
            try:
                if not self.path.is_file() or self.path.stat().st_size > 4096:
                    raise ValueError()
                state = json.loads(self.path.read_text())
                if (not isinstance(state, dict) or set(state) != set(self.state)
                        or type(state['version']) is not int or state['version'] != 1
                        or (state['hen'] is not None and not valid_hen(state['hen']))
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
        self.zeros = 0

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
        zeros = min(3, self.zeros + 1) if label == 'no_chicken' and eggs == 0 else 0
        events = []
        if streak >= 3:
            if target == 'no_chicken' and hen is not None:
                name = hen.capitalize() if hen != 'unknown' else 'Unknown'
                laid = state['baseline'] is not None and eggs > state['baseline']
                events.append(name + (' laid an Egg' if laid else ' left'))
                state['hen'] = None
                state['baseline'] = None
            elif target != 'no_chicken' and target != hen:
                if hen is not None and hen != 'unknown':
                    events.append(hen.capitalize() + ' left')
                # No empty frame between hens means hidden eggs cannot be attributed.
                if hen is None:
                    state['baseline'] = state['eggs']
                elif hen != 'unknown':
                    state['baseline'] = None
                state['hen'] = target
                events.append(target.capitalize() + ' arrived')

        if label == 'no_chicken' and state['hen'] is None:
            if zeros >= 3:
                if state['eggs'] is not None and state['eggs'] > 0:
                    n = state['eggs']
                    events.append('{} {} collected'.format(n, 'egg' if n == 1 else 'eggs'))
                state['eggs'] = 0
            elif eggs > 0:
                state['eggs'] = eggs
        if events:
            state['latest_event'] = events[-1]
        # Persist before changing memory or publishing. Failed writes cannot consume
        # a transition. atomic_json fsyncs both file and directory; no pickle input.
        safe_path(self.path.parent, self.path.name)
        atomic_json(self.path, state)
        self.state = state
        self.candidate, self.streak, self.zeros = candidate, streak, zeros
        return events
