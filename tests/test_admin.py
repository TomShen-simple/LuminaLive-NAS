import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

from app import admin, server


class AdminDataTest(unittest.TestCase):
    def test_dashboard_includes_offline_channels_and_latency_metrics(self) -> None:
        config = {
            "source_playlists": ["https://example.test/list.m3u"],
            "groups": [{"title": "卫视", "channels": [
                {"id": "DRAGON", "name": "东方卫视"},
                {"id": "HUNAN", "name": "湖南卫视"},
            ]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, status_path = root / "channels.json", root / "status.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            status_path.write_text(json.dumps({
                "ok": True,
                "state": "ready",
                "channels": {"DRAGON": {"validationMs": 450, "upstreamHost": "cdn.test"}},
            }), encoding="utf-8")
            with mock.patch.object(admin.settings, "CHANNELS_PATH", config_path), mock.patch.object(
                admin.settings, "STATUS_PATH", status_path
            ), mock.patch.object(admin.settings, "LOCAL_M3U_PATH", root / "local.m3u"):
                result = admin.dashboard()
        self.assertEqual(50, result["healthPercent"])
        self.assertEqual(450, result["averageValidationMs"])
        self.assertEqual([True, False], [item["online"] for item in result["channels"]])

    def test_local_m3u_round_trip(self) -> None:
        content = '#EXTM3U\n#EXTINF:-1 tvg-name="东方卫视" group-title="卫视",东方卫视\nhttps://cdn.test/live.m3u8\n'
        entries = admin.validate_m3u(content)
        self.assertEqual("东方卫视", entries[0]["name"])
        self.assertEqual("卫视", entries[0]["group"])

    def test_rejects_non_m3u_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "#EXTM3U"):
            admin.validate_m3u("https://example.test/live.m3u8")

    def test_source_list_deduplicates_urls(self) -> None:
        values = admin.validate_sources(["https://a.test/list.m3u", "https://a.test/list.m3u"])
        self.assertEqual(["https://a.test/list.m3u"], values)

    def test_persists_local_m3u_and_refresh_request(self) -> None:
        content = '#EXTM3U\n#EXTINF:-1 tvg-name="测试台",测试台\nhttps://cdn.test/live.m3u8\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(admin.settings, "LOCAL_M3U_PATH", root / "local.m3u"), mock.patch.object(
                admin.settings, "REFRESH_REQUEST_PATH", root / "refresh.request"
            ):
                saved = admin.save_local_m3u(content)
                queued = admin.request_refresh()
                self.assertTrue((root / "local.m3u").exists())
                self.assertTrue((root / "refresh.request").exists())
        self.assertTrue(saved["changed"])
        self.assertTrue(queued["queued"])

    def test_does_not_queue_duplicate_while_refreshing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / "status.json"
            status.write_text('{"refreshing": true}', encoding="utf-8")
            with mock.patch.object(admin.settings, "STATUS_PATH", status), mock.patch.object(
                admin.settings, "REFRESH_REQUEST_PATH", root / "refresh.request"
            ):
                result = admin.request_refresh()
                self.assertFalse((root / "refresh.request").exists())
        self.assertFalse(result["queued"])
        self.assertTrue(result["refreshing"])


class AdminAccessTest(unittest.TestCase):
    def test_private_client_allowed_without_token(self) -> None:
        request = mock.Mock(remote="192.168.1.30", headers={"X-Lumina-Admin": "1"})
        with mock.patch.object(server.settings, "ADMIN_TOKEN", ""):
            server.require_admin(request)

    def test_token_required_when_configured(self) -> None:
        request = mock.Mock(remote="192.168.1.30", headers={"X-Lumina-Admin": "1"})
        with mock.patch.object(server.settings, "ADMIN_TOKEN", "secret"):
            with self.assertRaises(web.HTTPUnauthorized):
                server.require_admin(request)


if __name__ == "__main__":
    unittest.main()
