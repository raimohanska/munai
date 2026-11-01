"""Credential-file tests use only synthetic temp files, never real credentials."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from munai_remote import DEFAULT_CONFIG, RemoteError, load_profiles, main, save_profiles


class CredentialFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='munai-credentials-test-')
        self.path = Path(self.temporary.name) / 'synthetic-credentials.json'
        self.profiles = {'green': {'url': 'https://example.invalid:8443',
                                   'fingerprint': 'a' * 64, 'token': 'synthetic-test-token-' + 'x' * 32}}

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_is_local_ignored_file(self):
        self.assertEqual(DEFAULT_CONFIG.name, '.munai-remote.json')
        self.assertEqual(DEFAULT_CONFIG.parent, Path(__file__).resolve().parent.parent)

    def test_round_trip_creates_owner_only_file_even_with_permissive_umask(self):
        before = os.umask(0)
        try:
            save_profiles(self.path, self.profiles)
        finally:
            os.umask(before)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_profiles(self.path), self.profiles)

    def test_group_or_world_readable_file_is_rejected(self):
        save_profiles(self.path, self.profiles)
        for mode in (0o644, 0o640, 0o606):
            self.path.chmod(mode)
            with self.assertRaises(RemoteError):
                load_profiles(self.path)
            with self.assertRaises(RemoteError):
                save_profiles(self.path, {})

    def test_symlinks_are_never_read_or_overwritten(self):
        target = self.path.parent / 'unrelated.json'
        target.write_text('unrelated synthetic data')
        self.path.symlink_to(target)
        with self.assertRaises(RemoteError):
            load_profiles(self.path)
        with self.assertRaises(RemoteError):
            save_profiles(self.path, {})
        self.assertEqual(target.read_text(), 'unrelated synthetic data')

    def test_environment_paths_rejected_before_open(self):
        with patch('munai_remote.os.open', side_effect=AssertionError('Must not open env path')):
            with self.assertRaises(RemoteError):
                load_profiles(self.path.parent / '.env')

    def test_invalid_json_does_not_echo_file_contents(self):
        save_profiles(self.path, {})
        self.path.write_text('synthetic-private-invalid-json')
        with self.assertRaises(RemoteError) as caught:
            load_profiles(self.path)
        self.assertNotIn('synthetic-private', str(caught.exception))

    def test_failed_atomic_replace_preserves_old_credentials_and_removes_temp(self):
        save_profiles(self.path, self.profiles)
        with patch('munai_remote.os.replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                save_profiles(self.path, {})
        self.assertEqual(load_profiles(self.path), self.profiles)
        self.assertEqual(list(self.path.parent.glob('.munai-remote.json.pending-*')), [])

    def test_legacy_profile_without_token_fails_without_network_access(self):
        save_profiles(self.path, {'green': {'url': 'https://example.invalid', 'fingerprint': 'a' * 64,
                                           'account': 'synthetic-old-account'}})
        with patch('munai_remote.Remote.connect', side_effect=AssertionError('No connection')), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(['--config', str(self.path), 'remote', 'status']), 1)
        self.assertIn('no valid local token', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
