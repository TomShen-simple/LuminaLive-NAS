import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import resolver


class PlaylistParsingTest(unittest.TestCase):
    def test_canonicalizes_common_channel_names(self) -> None:
        self.assertEqual("CCTV-5+", resolver.canonical_name("CCTV5+ 体育赛事[1080p]"))
        self.assertEqual("北京卫视", resolver.canonical_name("BRTV 北京卫视 (1080p)"))
        self.assertEqual("广东卫视", resolver.canonical_name("Guangdong Satellite TV"))

    def test_parses_extinf_and_url_pairs(self) -> None:
        text = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-name="东方卫视[1080p]" group-title="卫视",东方卫视\n'
            "https://example.test/dragon.m3u8\n"
        )
        result = resolver.parse_m3u(text)
        self.assertEqual(1, len(result))
        self.assertEqual("东方卫视", result[0].name)

    def test_rejects_private_upstream_by_default(self) -> None:
        with mock.patch.object(resolver.settings, "ALLOW_PRIVATE", False), mock.patch.object(
            resolver.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.10", 80))],
        ):
            self.assertFalse(resolver.host_allowed("http://nas.lan/live.m3u8"))


class RenderingTest(unittest.TestCase):
    def test_render_keeps_requested_order_and_direct_url(self) -> None:
        config = {
            "playlist_name": "Test",
            "source_playlists": [],
            "groups": [{
                "title": "卫视",
                "channels": [{"id": "DRAGON", "name": "东方卫视"}],
            }],
        }
        selected = {
            "DRAGON": resolver.Validated(
                url="https://cdn.example/live.m3u8",
                elapsed_ms=100,
                media_url="https://cdn.example/media.m3u8",
                segment_url="https://cdn.example/a.ts",
            )
        }
        rendered = resolver.render(config, selected)
        self.assertIn('group-title="卫视",东方卫视', rendered)
        self.assertIn("https://cdn.example/live.m3u8", rendered)

    def test_atomic_write_does_not_rewrite_equal_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlist.m3u"
            self.assertTrue(resolver.atomic_write(path, "#EXTM3U\n"))
            self.assertFalse(resolver.atomic_write(path, "#EXTM3U\n"))


if __name__ == "__main__":
    unittest.main()

