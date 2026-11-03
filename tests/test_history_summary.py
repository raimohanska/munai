"""Synthetic remote listings only, never real credential files or endpoints."""
import unittest
from unittest.mock import Mock

from munai_remote import history_summary, RemoteError


class HistorySummaryTests(unittest.TestCase):
    def test_coverage_sorted_independently_of_directory_order(self):
        remote = Mock()
        remote.request.side_effect = [
            {'files': [{'path': 'unknown/20000101T130000_000000Z.jpg', 'bytes': 100},
                       {'path': 'empty/20000101T120000_000000Z.jpg', 'bytes': 200}]},
            {'files': []}]
        result = history_summary(remote)
        self.assertEqual(result['captures'], {'files': 2, 'bytes': 300,
                         'oldest_utc': '20000101T120000_000000Z',
                         'newest_utc': '20000101T130000_000000Z'})
        self.assertEqual(result['results']['oldest_utc'], None)
        self.assertEqual([c.args[0] for c in remote.request.call_args_list], ['GET', 'GET'])

    def test_invalid_listing_rejected(self):
        for item in ({'path': 'empty/invalid.jpg', 'bytes': 100},
                     {'path': 'empty/20000101T120000_000000Z.jpg', 'bytes': -1}):
            remote = Mock()
            remote.request.return_value = {'files': [item]}
            with self.assertRaises(RemoteError):
                history_summary(remote)
