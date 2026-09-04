import time
import unittest
from unittest import mock

from aiohttp import web

from app.migu_relay import MiguRelay


class MiguRelayTest(unittest.TestCase):
    def setUp(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "MIGU_RELAY_TOKEN": "a" * 32,
                "MIGU_RELAY_SIGNING_SECRET": "b" * 32,
            },
        ):
            self.relay = MiguRelay()

    def test_asset_token_round_trip_and_tamper_rejection(self) -> None:
        url = "https://gslbmgsplive.miguvideo.com/live/seg.ts"
        token = self.relay.encode_asset(url)
        self.assertEqual(url, self.relay.decode_asset(token))
        with self.assertRaises(web.HTTPForbidden):
            self.relay.decode_asset(token[:-1] + ("A" if token[-1] != "A" else "B"))

    def test_manifest_urls_are_rewritten_to_local_proxy(self) -> None:
        manifest = '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nseg.ts\n'
        rewritten = self.relay.rewrite_manifest(
            manifest,
            "https://gslbmgsplive.miguvideo.com/live/index.m3u8",
        )
        self.assertNotIn("seg.ts\n", rewritten)
        self.assertNotIn('URI="init.mp4"', rewritten)
        self.assertEqual(2, rewritten.count("/api/migu/asset/"))

    def test_direct_mode_keeps_master_on_relay_but_media_on_migu_cdn(self) -> None:
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000\nquality/index.m3u8\n"
        rewritten_master = self.relay.rewrite_manifest(
            master,
            "https://gslbmgsplive.miguvideo.com/live/index.m3u8",
            direct_assets=True,
        )
        self.assertIn("/api/migu/asset/", rewritten_master)
        self.assertIn("?direct=1", rewritten_master)

        media = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nseg.ts\n'
        rewritten_media = self.relay.rewrite_manifest(
            media,
            "https://gslbmgsplive.miguvideo.com/live/quality/index.m3u8",
            direct_assets=True,
        )
        self.assertNotIn("/api/migu/asset/", rewritten_media)
        self.assertIn(
            'URI="https://gslbmgsplive.miguvideo.com/live/quality/init.mp4"',
            rewritten_media,
        )
        self.assertIn(
            "https://gslbmgsplive.miguvideo.com/live/quality/seg.ts",
            rewritten_media,
        )

    def test_official_http_media_host_is_allowed(self) -> None:
        self.assertTrue(
            self.relay.allowed_url("http://gslbmgsplive.miguvideo.com/live/index.m3u8")
        )
        self.assertFalse(self.relay.allowed_url("http://example.test/live/index.m3u8"))

    def test_android_dd_calcu_completes_official_media_url(self) -> None:
        url = "http://gslbmgsplive.miguvideo.com/live/index.m3u8?a=1&puData=0123456789"
        with mock.patch("app.migu_relay.time.localtime") as localtime:
            localtime.return_value.tm_year = 2026
            completed = self.relay.add_android_dd_calcu(url, "961023778")
        self.assertEqual(
            "http://gslbmgsplive.miguvideo.com/live/index.m3u8?"
            "a=1&puData=0123456789&ddCalcu=9081v72a63x54a&sv=10004&ct=android",
            completed,
        )

    def test_android_dd_calcu_rejects_missing_pudata(self) -> None:
        with self.assertRaises(web.HTTPBadGateway):
            self.relay.add_android_dd_calcu(
                "http://gslbmgsplive.miguvideo.com/live/index.m3u8",
                "961023778",
            )

    def test_untrusted_manifest_host_is_rejected(self) -> None:
        with self.assertRaises(web.HTTPBadGateway):
            self.relay.rewrite_manifest(
                "#EXTM3U\nhttps://example.test/seg.ts\n",
                "https://gslbmgsplive.miguvideo.com/live/index.m3u8",
            )

    def test_expired_asset_token_is_rejected(self) -> None:
        with mock.patch("app.migu_relay.time.time", return_value=time.time() - 1000):
            token = self.relay.encode_asset(
                "https://gslbmgsplive.miguvideo.com/live/seg.ts"
            )
        with self.assertRaises(web.HTTPForbidden):
            self.relay.decode_asset(token)


if __name__ == "__main__":
    unittest.main()
