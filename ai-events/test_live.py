import unittest
from unittest.mock import patch

from live import channel_inventory, relay_media


class LiveTests(unittest.TestCase):
    @patch('live.configured', return_value=[{'port': 12345}])
    @patch('live.Registry')
    def test_inventory_only_exposes_configured_loopback_relays(self, registry, _configured):
        urls = {
            'local': 'http://localhost:12345/live.m3u8',
            'foreign': 'http://example.test:12345/live.m3u8',
            'wrong-port': 'http://127.0.0.1:12346/live.m3u8',
            'signed': 'http://127.0.0.1:12345/live.m3u8?token=secret',
            'disabled': 'http://127.0.0.1:12345/live.m3u8',
        }
        registry.return_value.list.return_value = [
            {'channel_id': key, 'name': key, 'source_url': url, 'enabled': key != 'disabled'}
            for key, url in urls.items()
        ]
        self.assertEqual(channel_inventory(None, None)['channels'], [
            {'id': 'local', 'name': 'local', 'stream': '/api/live/local/live.m3u8'},
            {'id': 'foreign', 'name': 'foreign', 'stream': None},
            {'id': 'wrong-port', 'name': 'wrong-port', 'stream': None},
            {'id': 'signed', 'name': 'signed', 'stream': None},
        ])

    @patch('live.HTTPConnection')
    def test_invalid_segment_is_rejected_before_connecting(self, connection):
        with self.assertRaises(ValueError):
            relay_media(None, None, 'local', '../config.json')
        connection.assert_not_called()
