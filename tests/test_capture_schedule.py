"""Offline clock/option tests only; no camera capture functionality."""
import time
import unittest
from unittest.mock import patch

from ha_service.options import AppError, validate
from ha_service.schedule import capture_paused


def clock(hour, minute):
    return time.struct_time((2000, 1, 1, hour, minute, 0, 5, 1, -1))


class CaptureScheduleTests(unittest.TestCase):
    def test_default_midnight_window_and_boundaries(self):
        options = validate({})
        self.assertEqual(options['capture_pause_start'], '23:00')
        self.assertEqual(options['capture_pause_end'], '05:00')
        for hour, minute, paused in ((22, 59, False), (23, 0, True),
                                     (0, 0, True), (4, 59, True), (5, 0, False),
                                     (12, 0, False)):
            self.assertEqual(capture_paused(options, clock(hour, minute)), paused)

    def test_daytime_window(self):
        options = validate({'capture_pause_start': '12:30', 'capture_pause_end': '14:15'})
        for hour, minute, paused in ((12, 29, False), (12, 30, True),
                                     (14, 14, True), (14, 15, False), (23, 0, False)):
            self.assertEqual(capture_paused(options, clock(hour, minute)), paused)

    def test_matching_times_disable_pause(self):
        options = validate({'capture_pause_start': '05:00', 'capture_pause_end': '05:00'})
        for hour in range(24):
            self.assertFalse(capture_paused(options, clock(hour, 0)))

    def test_uses_local_clock_not_utc(self):
        with patch('ha_service.schedule.time.localtime', return_value=clock(23, 30)) as local:
            self.assertTrue(capture_paused(validate({})))
        local.assert_called_once_with()

    def test_strict_time_validation(self):
        for key in ('capture_pause_start', 'capture_pause_end'):
            for value in ('2300', '5:00', '24:00', '23:60', '23:00\n', '', None, True, 2300):
                with self.assertRaises(AppError):
                    validate({key: value})
