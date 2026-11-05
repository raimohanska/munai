"""Synthetic runtime failures only; never access a camera or real credentials."""
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2

from ha_service.diagnostics import exception_details, ROOT
from ha_service.options import AppError, validate
from ha_service.runtime import EventLog, Runtime
from ha_service.__main__ import main


SECRET = 'rtsp://synthetic-user:synthetic-password@camera.invalid/private'


def opencv_error():
    error = cv2.error(SECRET)
    error.code = -215
    error.func = 'remap'
    error.err = SECRET
    error.file = '/data/references/synthetic-private/file.jpg'
    return error


class DiagnosticTests(unittest.TestCase):
    def test_opencv_function_code_and_versions_without_raw_details(self):
        result = exception_details(opencv_error())
        for expected in ('cv2.error', 'code=-215', 'function=remap', 'NumPy', 'OpenCV'):
            self.assertIn(expected, result)
        for private in (SECRET, 'synthetic-password', '/data', 'file.jpg'):
            self.assertNotIn(private, result)

    def test_untrusted_opencv_function_and_code_are_not_logged(self):
        error = opencv_error()
        error.func = 'synthetic-private-token'
        error.code = SECRET
        result = exception_details(error)
        self.assertNotIn('synthetic-private-token', result)
        self.assertNotIn(SECRET, result)

    def test_source_locations_without_source_lines_locals_or_private_paths(self):
        public_path = str(ROOT / 'ha_service' / 'plumage.py')
        try:
            exec(compile('raise ValueError(secret)', public_path, 'exec'), {'secret': SECRET})
        except ValueError as error:
            result = exception_details(error)
        self.assertIn('ValueError', result)
        self.assertIn('ha_service/plumage.py:1', result)
        self.assertNotIn(str(ROOT), result)
        self.assertNotIn('raise ValueError', result)
        self.assertNotIn(SECRET, result)
        for path in ('/data/references/private/reference.jpg', str(ROOT / 'local-data' / 'private.py')):
            try:
                exec(compile('raise ValueError(secret)', path, 'exec'), {'secret': SECRET})
            except ValueError as error:
                result = exception_details(error)
            self.assertNotIn(path, result)
            self.assertNotIn('private', result)
            self.assertNotIn(SECRET, result)

    def test_chained_causes_remain_visible_without_messages(self):
        try:
            try:
                raise TypeError(SECRET)
            except TypeError as cause:
                raise RuntimeError(SECRET) from cause
        except RuntimeError as error:
            result = exception_details(error)
        self.assertIn('RuntimeError', result)
        self.assertIn('caused by TypeError', result)
        self.assertNotIn(SECRET, result)

    def test_custom_exception_class_name_is_not_an_untrusted_log_channel(self):
        private_type = type('synthetic_private_token', (Exception,), {})
        result = exception_details(private_type(SECRET))
        self.assertIn('Exception', result)
        self.assertNotIn('synthetic_private_token', result)
        self.assertNotIn(SECRET, result)

    def test_changed_diagnostic_is_logged_immediately_but_repeats_are_throttled(self):
        logger = Mock(spec=logging.Logger)
        events = EventLog(logger, lambda: 0)
        events.failed('round', 'Round failed.', 'TypeError')
        events.failed('round', 'Round failed.', 'TypeError')
        self.assertEqual(logger.warning.call_count, 1)
        events.failed('round', 'Round failed.', 'cv2.error code=-215 function=remap')
        self.assertEqual(logger.warning.call_count, 2)
        events.recovered('round', 'Recovered.')
        events.failed('round', 'Round failed.', 'TypeError')
        self.assertEqual(logger.warning.call_count, 3)


class RuntimeDiagnosticTests(unittest.TestCase):
    def runtime(self):
        runtime = Runtime(validate({'box_split': 'vertical'}), None)
        runtime.store = Mock()
        runtime.nest = Mock()
        runtime.journal = Mock()
        return runtime

    def test_reference_loader_keeps_diagnostics_and_specific_validation_errors(self):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
            root = Path(temporary)
            for name in ('hen_a', 'hen_b'):
                (root / name).mkdir()
            runtime.store = SimpleNamespace(reference_root=lambda: root, revision='synthetic')
            with patch('ha_service.runtime.split_recognition.load', side_effect=opencv_error()):
                with self.assertRaises(AppError) as caught:
                    runtime._references()
            self.assertEqual(str(caught.exception), 'Reference loading failed; check app logs.')
            self.assertIn('cv2.error', caught.exception.diagnostic)
            self.assertIn('function=remap', caught.exception.diagnostic)
            self.assertIn('ha_service/runtime.py:', caught.exception.diagnostic)
            self.assertNotIn(SECRET, caught.exception.diagnostic)
            self.assertIsNone(runtime.reference_revision)
            expected = AppError('Split mode requires reviewed full-frame empty references.')
            with patch('ha_service.runtime.split_recognition.load', side_effect=expected):
                with self.assertRaises(AppError) as caught:
                    runtime._references()
            self.assertIs(caught.exception, expected)

    def run_failure(self, error):
        runtime = self.runtime()
        runtime.options['capture_enabled'] = True
        runtime.update_daily = Mock()
        runtime.publish_daily = Mock()
        runtime.round = Mock(side_effect=error)
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch('ha_service.runtime.capture_paused', return_value=False), \
                self.assertLogs('munai', level='WARNING') as logs:
            runtime.run(stop)
        return logs

    def test_unexpected_round_error_is_logged_without_raw_traceback(self):
        logs = self.run_failure(opencv_error())
        text = '\n'.join(logs.output)
        self.assertIn('cv2.error', text)
        self.assertIn('function=remap', text)
        self.assertNotIn(SECRET, text)
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    def test_wrapped_round_failure_keeps_its_diagnostic(self):
        logs = self.run_failure(AppError('Reference loading failed; check app logs.',
                                         diagnostic=exception_details(opencv_error())))
        self.assertIn('cv2.error', '\n'.join(logs.output))

    def test_unexpected_startup_failure_has_safe_diagnostic(self):
        with patch('ha_service.__main__.logging.basicConfig'), \
                patch('ha_service.__main__.os.umask'), \
                patch('ha_service.__main__.read_options', side_effect=TypeError(SECRET)), \
                self.assertLogs('munai', level='ERROR') as logs:
            self.assertEqual(main(), 1)
        self.assertIn('TypeError', '\n'.join(logs.output))
        self.assertNotIn(SECRET, '\n'.join(logs.output))
