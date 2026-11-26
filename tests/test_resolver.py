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

    def test_cctv_url_affinity_does_not_confuse_ten_and_eleven(self) -> None:
        self.assertEqual(2, resolver.channel_url_affinity("CCTV10", "http://cdn.test/live/cctv10hd.m3u8"))
        self.assertEqual(0, resolver.channel_url_affinity("CCTV10", "http://cdn.test/live/cctv11hd.m3u8"))
        self.assertEqual(2, resolver.channel_url_affinity("CCTV11", "http://cdn.test/live/cctv11hd.m3u8"))

    def test_named_cctv_source_wins_over_faster_opaque_relay(self) -> None:
        opaque = resolver.Candidate("CCTV-10", "http://relay.test/0011_1.m3u8", 0)
        named = resolver.Candidate("CCTV-10", "http://cdn.test/live/cctv10hd.m3u8", 0)

        def checked(url: str) -> resolver.Validated:
            elapsed = 100 if "0011" in url else 900
            return resolver.Validated(url, elapsed, url, f"{url}/segment.ts")

        with mock.patch.object(resolver, "validate", side_effect=checked):
            channel_id, selected = resolver.resolve_channel("CCTV10", [opaque, named])

        self.assertEqual("CCTV10", channel_id)
        self.assertIsNotNone(selected)
        self.assertEqual(named.url, selected.url)

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

    def test_fetch_sample_truncates_full_200_segment_without_failing(self) -> None:
        ts_payload = (b"\x47" + b"\x00" * 187) * 2000  # ~376 KB full segment ignoring Range

        class FakeResponse:
            def __init__(self, payload: bytes, url: str) -> None:
                self._payload = payload
                self._pos = 0
                self._url = url

            def geturl(self) -> str:
                return self._url

            def read(self, size: int = -1) -> bytes:
                if size < 0:
                    chunk = self._payload[self._pos:]
                    self._pos = len(self._payload)
                    return chunk
                chunk = self._payload[self._pos:self._pos + size]
                self._pos += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        with mock.patch.object(resolver, "urlopen", return_value=FakeResponse(ts_payload, "https://cdn.test/seg.ts")):
            data, final_url, transfer_ms = resolver.fetch_sample("https://cdn.test/seg.ts", sample_bytes=65_536)
        self.assertEqual(65_536, len(data))
        self.assertEqual("https://cdn.test/seg.ts", final_url)
        self.assertGreaterEqual(transfer_ms, 1)
        self.assertTrue(resolver.looks_like_video_payload(data))

    def test_looks_like_video_payload_rejects_html_and_accepts_ts(self) -> None:
        self.assertFalse(resolver.looks_like_video_payload(b"<html><body>403 Forbidden</body></html>" * 30))
        self.assertTrue(resolver.looks_like_video_payload((b"\x47" + b"\x00" * 187) * 4))

    def test_local_m3u_beats_slow_remote_and_prefers_higher_throughput(self) -> None:
        local_slow = resolver.Candidate("浙江卫视", "http://local.test/slow.m3u8", -1)
        local_fast = resolver.Candidate("浙江卫视", "http://local.test/fast.m3u8", -1)
        remote = resolver.Candidate("CCTV-1", "http://remote.test/cctv1.m3u8", 0)

        results = {
            local_slow.url: resolver.Validated(local_slow.url, 300, local_slow.url, "s1.ts", throughput_mbps=2.4),
            local_fast.url: resolver.Validated(local_fast.url, 450, local_fast.url, "s2.ts", throughput_mbps=16.0),
            remote.url: resolver.Validated(remote.url, 200, remote.url, "s3.ts", throughput_mbps=12.0),
        }
        with mock.patch.object(resolver, "validate", side_effect=lambda u: results[u]):
            _, selected = resolver.resolve_channel("ZHEJIANG", [remote, local_slow, local_fast])
        self.assertIsNotNone(selected)
        self.assertEqual(local_fast.url, selected.url)

    def test_collect_candidates_keeps_local_m3u_ahead_of_remote_affinity(self) -> None:
        config = {
            "source_playlists": ["https://remote.test/list.m3u"],
            "groups": [{"title": "央视", "channels": [{"id": "CCTV1", "name": "CCTV-1"}]}],
        }
        remote_m3u = "#EXTM3U\n#EXTINF:-1,CCTV-1\nhttps://remote.test/live/cctv1.m3u8\n"
        local_m3u = "#EXTM3U\n#EXTINF:-1,CCTV-1\nhttps://local.test/hls/1000.m3u8\n"
        with tempfile.TemporaryDirectory() as directory:
            local_path = Path(directory) / "local.m3u"
            local_path.write_text(local_m3u, encoding="utf-8")
            with mock.patch.object(resolver.settings, "LOCAL_M3U_PATH", local_path), mock.patch.object(
                resolver.settings, "EXTRA_M3U_URLS", ()
            ), mock.patch.object(resolver, "host_allowed", return_value=True), mock.patch.object(
                resolver, "fetch", return_value=(remote_m3u.encode("utf-8"), "https://remote.test/list.m3u")
            ):
                pool = resolver.collect_candidates(config)
        self.assertEqual(
            ["https://local.test/hls/1000.m3u8", "https://remote.test/live/cctv1.m3u8"],
            [item.url for item in pool["CCTV1"]],
        )

    def test_validate_rejects_low_throughput_segment_and_supports_direct_ts(self) -> None:
        ts_chunk = (b"\x47" + b"\x00" * 187) * 200  # 37.6 KB
        playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg1.ts\n#EXTINF:4.0,\nseg2.ts\n"
        with mock.patch.object(resolver, "host_allowed", return_value=True), mock.patch.object(
            resolver, "fetch", return_value=(playlist.encode("utf-8"), "https://cdn.test/live/index.m3u8")
        ), mock.patch.object(
            resolver, "fetch_sample", return_value=(ts_chunk, "https://cdn.test/live/seg1.ts", 500)
        ), mock.patch.object(resolver.settings, "MIN_STREAM_MBPS", 2.0):
            # 37.6 KB in 500ms = 0.60 Mbps -> rejected
            self.assertIsNone(resolver.validate("https://cdn.test/live/index.m3u8"))

        with mock.patch.object(resolver, "host_allowed", return_value=True), mock.patch.object(
            resolver, "fetch_sample", return_value=(ts_chunk, "https://cdn.test/live/4k.ts", 50)
        ), mock.patch.object(resolver.settings, "MIN_STREAM_MBPS", 2.0):
            # 37.6 KB in 50ms = 6.02 Mbps -> accepted direct .ts
            result = resolver.validate("https://cdn.test/live/4k.ts")
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.throughput_mbps, 5.0)


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
