import unittest
from unittest import mock

from app.official_server import create_app


class OfficialServerTest(unittest.TestCase):
    def test_requires_credentials(self):
        with mock.patch.dict('os.environ', {'MIGU_RELAY_TOKEN': '', 'MIGU_RELAY_SIGNING_SECRET': ''}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_has_no_public_source_or_admin_routes(self):
        with mock.patch.dict('os.environ', {'MIGU_RELAY_TOKEN': 'a' * 32}):
            app = create_app()
        paths = {resource.canonical for resource in app.router.resources()}
        self.assertIn('/api/migu/playlist.m3u', paths)
        self.assertNotIn('/live/{filename}', paths)
        self.assertNotIn('/api/source-playlists', paths)
