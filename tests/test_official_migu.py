import asyncio
import time
import unittest
from unittest import mock

from aiohttp import web

from app.official_migu import MonitoredMiguRelay


PID = "641886773"
ROOT = "https://gslbmgsplive.miguvideo.com/root.m3u8"
MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:9\n#EXTINF:10,\nseg9.ts\n"


class RecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with mock.patch.dict("os.environ", {"MIGU_RELAY_TOKEN": "a" * 32}):
            self.relay = MonitoredMiguRelay()

    async def test_manifest_failure_invalidates_old_url_then_retries_once(self):
        self.relay.resolved[PID] = ("old", time.monotonic())
        async def resolve(pid):
            return self.relay.resolved.get(pid, ("new", 0))[0]
        async def flatten(url):
            if url == "old":
                raise web.HTTPBadGateway()
            return MEDIA, ROOT
        with mock.patch.object(self.relay, "resolve", side_effect=resolve), mock.patch.object(
            self.relay, "flatten", side_effect=flatten
        ) as fetch:
            result = await self.relay.media_manifest(PID)
        self.assertEqual((MEDIA, ROOT), result)
        self.assertEqual([mock.call("old"), mock.call("new")], fetch.call_args_list)
        self.assertEqual(1, self.relay.health[PID]["refreshCount"])

    async def test_concurrent_requests_share_one_recovery(self):
        calls = 0
        async def flatten(url):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            if calls == 1:
                raise web.HTTPBadGateway()
            return MEDIA, ROOT
        with mock.patch.object(self.relay, "resolve", new=mock.AsyncMock(return_value=ROOT)), mock.patch.object(
            self.relay, "flatten", side_effect=flatten
        ):
            results = await asyncio.gather(*(self.relay.media_manifest(PID) for _ in range(10)))
        self.assertEqual(2, calls)
        self.assertTrue(all(x == (MEDIA, ROOT) for x in results))

    async def test_persistent_failure_is_bounded_and_does_not_publish(self):
        with mock.patch.object(self.relay, "resolve", new=mock.AsyncMock(side_effect=web.HTTPBadGateway())) as fetch:
            with self.assertRaises(web.HTTPServiceUnavailable):
                await self.relay.media_manifest(PID)
            self.assertEqual(2, fetch.await_count)
            with self.assertRaises(web.HTTPServiceUnavailable):
                await self.relay.media_manifest(PID)
            self.assertEqual(3, fetch.await_count)
        self.assertFalse(self.relay.health[PID]["ok"])
        self.assertNotIn(PID, self.relay.media)

    async def test_sample_failure_refreshes_even_with_fresh_manifest(self):
        self.relay.media[PID] = (MEDIA, ROOT, time.monotonic())
        with mock.patch.object(self.relay, "resolve", new=mock.AsyncMock(return_value=ROOT)), mock.patch.object(
            self.relay, "flatten", new=mock.AsyncMock(return_value=(MEDIA, ROOT))
        ), mock.patch.object(self.relay, "check_segment", new=mock.AsyncMock(side_effect=[web.HTTPBadGateway(), None])) as sample:
            await self.relay.media_manifest(PID, sample=True)
        self.assertEqual(2, sample.await_count)
        self.assertTrue(self.relay.health[PID]["ok"])

    async def test_old_generation_failure_does_not_invalidate_new_manifest(self):
        self.relay.media[PID] = (MEDIA, ROOT, time.monotonic())
        with mock.patch.object(self.relay, "resolve", new=mock.AsyncMock()) as resolve:
            result = await self.relay.media_manifest(PID, failed_generation="https://old.miguvideo.com/expired.m3u8")
        self.assertEqual((MEDIA, ROOT), result)
        resolve.assert_not_awaited()

    async def test_playlist_only_contains_recent_media_verified_channels(self):
        self.relay.health[PID] = {"ok": True, "checkedMonotonic": time.monotonic()}
        self.relay.health["608807420"] = {"ok": False}
        request = mock.Mock(query={"access_token": "a" * 32}, headers={})
        response = await self.relay.official_playlist(request)
        self.assertIn("CCTV5+", response.text)
        self.assertNotIn("CCTV1", response.text)
        self.assertNotIn("央视频", response.text)
        self.assertNotIn("http://", response.text)

    async def test_expired_health_is_not_published(self):
        self.relay.health[PID] = {"ok": True, "checkedMonotonic": time.monotonic() - 1000}
        request = mock.Mock(query={"access_token": "a" * 32}, headers={})
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.relay.official_playlist(request)

    async def test_flatten_rejects_ended_and_encrypted_streams(self):
        for text in [MEDIA + "#EXT-X-ENDLIST\n", MEDIA + '#EXT-X-KEY:METHOD=AES-128,URI="key"\n']:
            with mock.patch.object(self.relay, "fetch_manifest", new=mock.AsyncMock(return_value=(text, ROOT))):
                with self.assertRaises(web.HTTPBadGateway):
                    await self.relay.flatten(ROOT)

    async def test_asset_signature_binds_channel_identity(self):
        token = self.relay.encode_asset(ROOT.replace("m3u8", "ts"), PID)
        self.assertEqual(PID, self.relay.decode_asset_payload(token)["program"])
        body, sig = token.split(".")
        with self.assertRaises(web.HTTPForbidden):
            self.relay.decode_asset_payload(body + "." + ("B" if sig[0] != "B" else "A") + sig[1:])

    async def test_unknown_program_is_rejected_without_creating_state(self):
        with self.assertRaises(web.HTTPNotFound):
            await self.relay.media_manifest("999999999")
        self.assertFalse(self.relay.locks)

    async def test_http_200_error_page_is_not_healthy_video(self):
        response = mock.Mock(status=200, url=ROOT)
        response.content.read = mock.AsyncMock(return_value=b'<html>access denied</html>')
        context = mock.MagicMock()
        context.__aenter__ = mock.AsyncMock(return_value=response)
        context.__aexit__ = mock.AsyncMock(return_value=False)
        self.relay.http = mock.Mock()
        self.relay.http.get.return_value = context
        with self.assertRaises(web.HTTPBadGateway):
            await self.relay.check_segment(MEDIA, ROOT, PID)
        self.assertNotIn(PID, self.relay.progress)

    async def test_signed_query_rotation_does_not_hide_frozen_sequence(self):
        response = mock.Mock(status=206, url=ROOT)
        response.content.read = mock.AsyncMock(return_value=(b'\x47' + b'\x00' * 187) * 3)
        context = mock.MagicMock()
        context.__aenter__ = mock.AsyncMock(return_value=response)
        context.__aexit__ = mock.AsyncMock(return_value=False)
        self.relay.http = mock.Mock()
        self.relay.http.get.return_value = context
        await self.relay.check_segment(MEDIA.replace('seg9.ts', 'seg9.ts?token=first'), ROOT, PID)
        marker, _ = self.relay.progress[PID]
        self.relay.progress[PID] = (marker, time.monotonic() - 60)
        with self.assertRaises(web.HTTPBadGateway):
            await self.relay.check_segment(MEDIA.replace('seg9.ts', 'seg9.ts?token=second'), ROOT, PID)

    async def test_media_403_triggers_refresh_but_never_substitutes_segment(self):
        token = self.relay.encode_asset(ROOT.replace('root.m3u8', 'seg9.ts'), PID)
        request = mock.Mock(match_info={'token': token})
        self.relay.media[PID] = (MEDIA, ROOT, time.monotonic())
        with mock.patch('app.migu_relay.MiguRelay.asset', new=mock.AsyncMock(side_effect=web.HTTPBadGateway())), mock.patch.object(
            self.relay, 'media_manifest', new=mock.AsyncMock(return_value=(MEDIA, ROOT))
        ) as repair:
            with self.assertRaises(web.HTTPServiceUnavailable):
                await self.relay.asset(request)
        repair.assert_awaited_once_with(PID, failed_generation=ROOT)


if __name__ == "__main__":
    unittest.main()
