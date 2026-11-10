"""Replayable reset intent; never deletes datasets or journal history."""
import json

from .egg_tracker import empty_tracker
from .nest import initial_state, valid_daily, valid_hen
from .options import AppError
from .storage import atomic_json, safe_path, sync_directory


def recover(root, layout):
    path = safe_path(root, 'state-reset.json')
    if not path.exists():
        return False
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        intent = json.loads(path.read_text())
        if (not isinstance(intent, dict) or set(intent) != {'version', 'layout', 'daily'}
                or type(intent['version']) is not int or intent['version'] != 1
                or intent['layout'] != layout or layout not in ('none', 'vertical', 'horizontal')
                or not valid_daily(intent['daily']) or intent['daily']['date'] is None
                or any(intent['daily']['counts'].values())):
            raise ValueError()
    except Exception:
        raise AppError('State reset is incomplete or invalid; restore a matching backup and configuration.') from None
    state = initial_state()
    state['daily_eggs'] = intent['daily']
    state['egg_tracking'] = dict(empty_tracker(), baseline_pending=True)
    atomic_json(safe_path(root, 'nest-state.json'), state)
    if layout != 'none':
        sides = ('left', 'right') if layout == 'vertical' else ('top', 'bottom')
        for zone in sides + ('center',):
            folder = safe_path(root, 'nests/' + layout + '/' + zone)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(safe_path(folder, 'nest-state.json'), state)
        atomic_json(safe_path(root, 'nest-layout.json'), {
            'version': 1, 'layout': layout, 'latest_event': None, 'latest_id': None,
            'carry': intent['daily']})
    # An old interrupted region transition must not resurrect pre-reset counts.
    transition = safe_path(root, 'nest-transition.json')
    if transition.exists():
        transition.unlink()
    path.unlink()
    sync_directory(root)
    return True


def begin(root, layout, daily):
    if (not valid_daily(daily) or daily['date'] is None
            or not all(valid_hen(name) for name in daily['counts'])):
        raise AppError('Invalid state reset totals.')
    zeros = {'date': daily['date'], 'counts': {name: 0 for name in daily['counts']}}
    atomic_json(safe_path(root, 'state-reset.json'), {'version': 1, 'layout': layout, 'daily': zeros})
    recover(root, layout)
