"""Service log formatting only; no camera or credential access."""
import logging
import os
import time
import unittest
from unittest.mock import patch

from ha_service.__main__ import main
from ha_service.options import AppError


class ServiceLoggingTests(unittest.TestCase):
    def test_local_timestamp_on_startup_error_and_shutdown(self):
        with patch('ha_service.__main__.logging.basicConfig') as configure, \
                patch('ha_service.__main__.os.umask'), \
                patch('ha_service.__main__.read_options', side_effect=AppError('Synthetic failure.')), \
                self.assertLogs('munai', level='INFO') as logs:
            self.assertEqual(main(), 1)

        settings = configure.call_args.kwargs
        formatter = logging.Formatter(settings['format'], settings['datefmt'])
        self.assertEqual([record.levelname for record in logs.records], ['ERROR', 'INFO'])
        try:
            # A fixed POSIX timezone needs no host timezone database. This instant
            # is midnight UTC but the previous evening in local time.
            with patch.dict(os.environ, {'TZ': 'EST5'}):
                time.tzset()
                for record in logs.records:
                    record.created = 946684800
                self.assertEqual(formatter.format(logs.records[0]),
                                 '1999-12-31 19:00:00 -0500 ERROR: Synthetic failure.')
                self.assertEqual(formatter.format(logs.records[1]),
                                 '1999-12-31 19:00:00 -0500 INFO: Munai stopped.')
        finally:
            time.tzset()
