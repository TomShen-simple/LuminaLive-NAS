import unittest
import base64
import json
import os
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.official_channels import MIGU_CHANNELS
from app.official_gateway import (GatewayGuard, LEGACY_CCTV_PROGRAMS, asset_program, create_app,
                                  media_assets, media_prefix_ok, read_prefix, rewrite_playlist)


class RewriteTest(unittest.TestCase):
    def test_legacy_cctv_routes_match_official_catalogue(self):
        expected = {
            name.lower().replace("+", "plus"): pid
            for pid, name in MIGU_CHANNELS.items() if name.startswith("CCTV")
        }
        self.assertEqual(expected, LEGACY_CCTV_PROGRAMS)
        self.assertNotIn("cctv16", LEGACY_CCTV_PROGRAMS)

    def test_legacy_playlist_keeps_key_private(self):
        source = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:6,\n/api/migu/asset/abc.def\n'
        result = rewrite_playlist(source, '/live-auto/cctv5')
        self.assertIn('/live-auto/cctv5/api/migu/asset/abc.def', result)
        self.assertNotIn('live-official', result)

    def test_live_start_hint_preserves_segments(self):
        source = '#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:42\n#EXTINF:10,\n/api/migu/asset/abc.def\n'
        result = rewrite_playlist(source, '/prefix')
        self.assertIn('#EXT-X-START:TIME-OFFSET=-30,PRECISE=NO', result)
        self.assertIn('#EXT-X-MEDIA-SEQUENCE:42', result)
        self.assertIn('/prefix/api/migu/asset/abc.def', result)
        self.assertNotIn('#EXT-X-START:', rewrite_playlist(source + '#EXT-X-ENDLIST\n', '/prefix'))

    def test_existing_start_hint_is_preserved(self):
        source = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-START:TIME-OFFSET=-24\n#EXTINF:6,\n/api/migu/asset/abc.def\n'
        result = rewrite_playlist(source, '/prefix')
        self.assertEqual(result.count('#EXT-X-START:'), 1)
        self.assertIn('TIME-OFFSET=-24', result)

    def test_removes_nas_credentials(self):
        result = rewrite_playlist('#EXTM3U\n#EXTINF:-1,CCTV1\n/api/migu/608807420/index.m3u8?access_token=PRIVATE\n',
                                  'https://example.test/live-official/PLAYKEY')
        self.assertNotIn('PRIVATE', result)
        self.assertIn('https://example.test/live-official/PLAYKEY/api/migu/608807420/index.m3u8', result)

    def test_preserves_signed_asset(self):
        result = rewrite_playlist('#EXTM3U\n#EXT-X-MAP:URI="/api/migu/asset/abc.def"\n/api/migu/asset/xyz.sig\n', '/prefix')
        self.assertIn('URI="/prefix/api/migu/asset/abc.def"', result)
        self.assertIn('/prefix/api/migu/asset/xyz.sig', result)

    def test_rejects_external_and_arbitrary_urls(self):
        for value in ['https://other.test/x', '//other.test/x', '/api/admin', '/api/migu/asset/../secret']:
            with self.assertRaises(ValueError):
                rewrite_playlist('#EXTM3U\n' + value, '/prefix')

    def test_rejects_non_playlist(self):
        with self.assertRaises(ValueError):
            rewrite_playlist('<html>oops</html>', '/prefix')

    def test_ad_markers_and_bad_media_are_rejected(self):
        path = '/api/migu/asset/' + base64.urlsafe_b64encode(json.dumps({'program':'641886683'}).encode()).decode().rstrip('=') + '.sig'
        source = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:6,\n' + path + '\n'
        self.assertEqual([path], media_assets(source))
        self.assertEqual('641886683', asset_program(path))
        for marker in ['#EXT-X-CUE-OUT:30', '#EXT-X-DATERANGE:CLASS="com.apple.hls.interstitial"']:
            with self.assertRaises(ValueError):
                media_assets(source.replace('#EXTINF:', marker + '\n#EXTINF:'))
        self.assertFalse(media_prefix_ok(b'<html>advert</html>' * 30))
        self.assertTrue(media_prefix_ok((b'\x47' + b'\x00' * 187) * 3))


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_alias_rejects_unknown_and_cross_channel_assets(self):
        settings = {
            'CCTV_MIGU_RELAY_BASE': 'http://127.0.0.1:18786',
            'CCTV_MIGU_RELAY_TOKEN': 'test-relay-token',
            'OFFICIAL_PLAYBACK_KEY': 'test-playback-key-at-least-24-characters',
            'OFFICIAL_PUBLIC_BASE': 'https://example.test/live-official',
        }
        with mock.patch.dict(os.environ, settings):
            async with TestClient(TestServer(create_app())) as client:
                response = await client.get('/live-auto/cctv16/index.m3u8')
                self.assertEqual(404, response.status)
                other = base64.urlsafe_b64encode(json.dumps({'program': '608807420'}).encode()).decode().rstrip('=')
                response = await client.get('/live-auto/cctv5/api/migu/asset/' + other + '.sig')
                self.assertEqual(404, response.status)

    async def test_probe_before_authorizing_and_quarantine_failure(self):
        pid = '641886683'
        path = '/api/migu/asset/' + base64.urlsafe_b64encode(json.dumps({'program':pid}).encode()).decode().rstrip('=') + '.sig'
        source = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:6,\n' + path + '\n'
        guard = GatewayGuard()
        self.assertFalse(guard.authorized(path))
        response = mock.Mock(status=206)
        content = mock.Mock()
        chunks = [b'\x47' + b'\x00' * 187, (b'\x47' + b'\x00' * 187) * 2, b'']
        content.read = mock.AsyncMock(side_effect=chunks)
        response.content = content
        context = mock.MagicMock()
        context.__aenter__ = mock.AsyncMock(return_value=response)
        context.__aexit__ = mock.AsyncMock(return_value=None)
        http = mock.Mock()
        http.get.return_value = context
        await guard.inspect(pid, source, http, 'http://127.0.0.1:18786', {'Authorization':'Bearer hidden'})
        self.assertTrue(guard.authorized(path))
        self.assertTrue(guard.last_result[pid]['ok'])
        with self.assertRaises(web.HTTPServiceUnavailable):
            await guard.inspect(pid, source.replace('#EXTINF:', '#EXT-X-CUE-OUT:6\n#EXTINF:'), http,
                                'http://127.0.0.1:18786', {})
        self.assertFalse(guard.authorized(path))
        self.assertFalse(guard.last_result[pid]['ok'])
