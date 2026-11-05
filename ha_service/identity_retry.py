"""Bounded best-effort identity selection from fresh appearance decisions."""
import copy

MAX_EXTRA_SHOTS = 15


def known(item):
    return not item.get('obscured') and item['label'] not in ('unknown', 'no_chicken')


class IdentityRetry:
    def __init__(self):
        self.selected = {}
        self.prediction = None
        self.evidence = None

    def add(self, prediction, evidence):
        self.prediction = copy.deepcopy(prediction)
        self.evidence = evidence
        items = prediction.get('zones', {'whole': prediction})
        for zone, item in items.items():
            # Never carry a visitor across an empty or obscured observation.
            if known(item) or item['label'] == 'no_chicken' or item.get('obscured'):
                self.selected[zone] = (copy.deepcopy(item), evidence)
            elif zone not in self.selected or not known(self.selected[zone][0]):
                self.selected[zone] = (copy.deepcopy(item), evidence)

    def needs_more(self):
        return any(item['label'] == 'unknown' and not item.get('obscured')
                   for item, unused in self.selected.values())

    def result(self):
        if 'zones' not in self.prediction:
            return self.selected['whole'][0], self.selected['whole'][1], None
        prediction = copy.deepcopy(self.prediction)
        prediction['zones'] = {zone: item for zone, (item, unused) in self.selected.items()}
        labels = {item['label'] for item in prediction['zones'].values()
                  if item['label'] != 'no_chicken'}
        prediction['label'] = (next(iter(labels)) if len(labels) == 1 else
                               'unknown' if labels else 'no_chicken')
        counts = [item['egg_count'] for zone, item in prediction['zones'].items()
                  if zone != 'center']
        prediction['egg_count'] = sum(counts) if all(n is not None for n in counts) else None
        return prediction, self.evidence, {zone: frame for zone, (unused, frame) in self.selected.items()}
