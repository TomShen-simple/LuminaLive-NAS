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
