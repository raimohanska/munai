"""Upgrade compatibility for retired app settings; no camera access."""
import json
from pathlib import Path
import unittest

from ha_service.options import validate


class RetiredOptionsTests(unittest.TestCase):
    def test_saved_pause_settings_are_ignored(self):
        defaults = validate({})
        for value in ('23:00', '05:00', '', None, True, 2300):
            self.assertEqual(validate({'capture_pause_start': value,
                                       'capture_pause_end': value}), defaults)

    def test_pause_settings_are_not_exposed(self):
        root = Path(__file__).resolve().parent.parent
        config = json.loads((root / 'config.json').read_text())
        translations = json.loads((root / 'translations' / 'en.json').read_text())
        for key in ('capture_pause_start', 'capture_pause_end'):
            self.assertNotIn(key, config['options'])
            self.assertNotIn(key, config['schema'])
            self.assertNotIn(key, translations['configuration'])
