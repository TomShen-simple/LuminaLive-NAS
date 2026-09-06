from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import urlencode, urljoin

from aiohttp import ClientError, ClientTimeout, web

from .migu_relay import MediaStreamInterrupted, MiguRelay
from .official_channels import MIGU_CHANNELS


class MonitoredMiguRelay(MiguRelay):
    """NAS-side official resolution, bounded recovery and per-channel checks."""

    def __init__(self) -> None:
        super().__init__()
        self.active_interval = max(5, int(os.environ.get("MIGU_ACTIVE_CHECK_SECONDS", "15")))
        self.idle_interval = max(60, int(os.environ.get("MIGU_IDLE_CHECK_SECONDS", "300")))
        self.manifest_timeout = max(3, int(os.environ.get("MIGU_MANIFEST_TIMEOUT", "8")))
        self.programs = dict(MIGU_CHANNELS)
        self.locks: dict[str, asyncio.Lock] = {}
        self.media: dict[str, tuple[str, str, float]] = {}
        self.activity: dict[str, float] = {}
        self.health: dict[str, dict] = {}
        self.last_repair: dict[str, float] = {}
        self.progress: dict[str, tuple[str, float]] = {}
        self.monitor_task: asyncio.Task | None = None
        self.check_slots = asyncio.Semaphore(3)
        self.idle_slots = asyncio.Semaphore(2)
        self.check_tasks: dict[str, asyncio.Task] = {}
        self.check_active: dict[str, bool] = {}

    async def start(self, app: web.Application) -> None:
        await super().start(app)
        if self.enabled:
            self.monitor_task = asyncio.create_task(self.monitor())

    async def stop(self, app: web.Application) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        for task in self.check_tasks.values():
            task.cancel()
        await asyncio.gather(*self.check_tasks.values(), return_exceptions=True)
        await super().stop(app)

    def status(self) -> dict:
        return {
            **super().status(),
            "provider": "migu", "fetchLocation": "nas", "mediaMode": "nas-relay",
            "activeCheckSeconds": self.active_interval,
            "idleCheckSeconds": self.idle_interval,
            "providers": {"migu": "configured" if self.enabled else "disabled",
                          "yangshipin": "not_implemented"},
            "healthyChannels": sum(bool(s.get("ok")) and time.monotonic() - s.get("checkedMonotonic", 0)
                                   <= self.idle_interval + 60 for s in self.health.values()),
            "channels": {pid: {"name": self.programs.get(pid, pid), **state}
                         for pid, state in self.health.items()},
        }

    async def fetch_manifest(self, url: str) -> tuple[str, str]:
        # Bound the urllib worker itself, not only its asyncio waiter.
        def fetch() -> tuple[bytes, str, int]:
            from urllib.request import Request, urlopen
            req = Request(url, headers=self.upstream_headers())
            with urlopen(req, timeout=self.manifest_timeout) as response:
                return response.read(1_000_001), response.geturl(), response.status

        try:
            data, final_url, status = await asyncio.to_thread(fetch)
            if status != 200 or len(data) > 1_000_000 or not self.allowed_url(final_url):
                raise ValueError("invalid manifest response")
            text = data.decode("utf-8-sig", errors="replace")
            if not text.startswith("#EXTM3U"):
                raise ValueError("not HLS")
            return text, final_url
        except (OSError, ValueError, TimeoutError) as exc:
            # Do not expose signed URLs, cookies or server response bodies.
            raise web.HTTPBadGateway(text=f"Migu manifest {type(exc).__name__}") from exc

    async def resolve(self, program_id: str) -> str:
        if program_id not in self.programs:
            raise web.HTTPNotFound(text="unknown official channel")
        # API failures are bounded even when the shared media client streams
        # without a total timeout.
        return await asyncio.wait_for(super().resolve(program_id), timeout=12)

    async def flatten(self, root: str) -> tuple[str, str]:
        url = root
        for _ in range(4):
            text, url = await self.fetch_manifest(url)
            if "#EXT-X-KEY:" in text or "#EXT-X-SESSION-KEY:" in text:
                raise web.HTTPBadGateway(text="encrypted official stream unsupported")
            if "#EXTINF:" in text:
                if "#EXT-X-ENDLIST" in text:
                    raise web.HTTPBadGateway(text="official live stream ended")
                if not any(x.strip() and not x.startswith("#") for x in text.splitlines()):
                    raise web.HTTPBadGateway(text="empty official media playlist")
                return text, url
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            variants = []
            for i, line in enumerate(lines[:-1]):
                if line.startswith("#EXT-X-STREAM-INF:") and not lines[i + 1].startswith("#"):
                    # Separate audio renditions need a master playlist and are
                    # not silently discarded by the flattening path.
                    if 'AUDIO="' in line:
                        continue
                    match = re.search(r"(?:^|,)BANDWIDTH=(\d+)", line.split(":", 1)[1])
                    variants.append((int(match[1]) if match else 0, urljoin(url, lines[i + 1])))
            if not variants:
                raise web.HTTPBadGateway(text="unsupported official master playlist")
            ceiling = int(os.environ.get("MAX_BANDWIDTH", "10000000"))
            eligible = [x for x in variants if x[0] <= ceiling]
            url = (max(eligible) if eligible else min(variants))[1]
            if not self.allowed_url(url):
                raise web.HTTPBadGateway(text="untrusted official variant host")
        raise web.HTTPBadGateway(text="official playlist nesting limit")

    async def media_manifest(self, program_id: str, *, sample: bool = False,
                             failed_generation: str | None = None) -> tuple[str, str]:
        if program_id not in self.programs:
            raise web.HTTPNotFound(text="unknown official channel")
        async with self.locks.setdefault(program_id, asyncio.Lock()):
            cached = self.media.get(program_id)
            now = time.monotonic()
            if failed_generation and (not cached or cached[1] == failed_generation):
                if now - self.last_repair.get(program_id, float("-inf")) >= 5:
                    self.resolved.pop(program_id, None)
                    self.media.pop(program_id, None)
                    self.last_repair[program_id] = now
                    cached = None
            if cached and now - cached[2] < 2 and not sample:
                return cached[0], cached[1]
            started = time.monotonic()
            for attempt in range(2):
                try:
                    root = await self.resolve(program_id)
                    text, final_url = await self.flatten(root)
                    if sample:
                        await self.check_segment(text, final_url, program_id)
                    self.media[program_id] = (text, final_url, time.monotonic())
                    state = self.health.setdefault(program_id, {})
                    state.update(manifestOk=True, lastManifestAt=time.time(), lastError="",
                                 manifestMs=round((time.monotonic() - started) * 1000))
                    if sample:
                        state.update(ok=True, checkedAt=time.time(), checkedMonotonic=time.monotonic())
                    return text, final_url
                except (web.HTTPException, ClientError, OSError, TimeoutError) as exc:
                    now = time.monotonic()
                    self.media.pop(program_id, None)
                    if attempt == 0 and now - self.last_repair.get(program_id, float("-inf")) >= 5:
                        self.resolved.pop(program_id, None)
                        self.last_repair[program_id] = now
                        state = self.health.setdefault(program_id, {})
                        state["refreshCount"] = state.get("refreshCount", 0) + 1
                        continue
                    self.health.setdefault(program_id, {}).update(
                        ok=False, manifestOk=False, checkedAt=time.time(),
                        checkedMonotonic=now, lastError=type(exc).__name__)
                    raise web.HTTPServiceUnavailable(text="official channel temporarily unavailable",
                                                     headers={"Retry-After": "2"}) from exc
            raise AssertionError("unreachable")

    async def check_segment(self, text: str, base: str, program_id: str) -> None:
        segments = [urljoin(base, line.strip()) for line in text.splitlines()
                    if line.strip() and not line.startswith("#")]
        url = segments[-1]
        if not self.allowed_url(url):
            raise web.HTTPBadGateway(text="untrusted official media host")
        assert self.http is not None
        headers = {**self.upstream_headers(), "Range": "bytes=0-4095"}
        async with self.http.get(url, headers=headers, timeout=ClientTimeout(total=8)) as response:
            if response.status not in (200, 206) or not self.allowed_url(str(response.url)):
                raise web.HTTPBadGateway(text="official segment unavailable")
            # read(n) can return a tiny first network chunk. Fill the bounded
            # probe before deciding that a fragmented response is not video.
            chunks = bytearray()
            while len(chunks) < 4096:
                chunk = await response.content.read(4096 - len(chunks))
                if not chunk:
                    break
                chunks.extend(chunk)
            sample = bytes(chunks)
        # Reject HTML/JSON status pages. Supported official transport streams
        # have consecutive MPEG-TS sync bytes; fMP4 has a recognizable box.
        ts = any(sample[i] == 0x47 and sample[i + 188] == 0x47
                 for i in range(min(188, max(0, len(sample) - 188))))
        mp4 = len(sample) >= 12 and sample[4:8] in (b"ftyp", b"styp", b"moof", b"sidx")
        if not (ts or mp4):
            raise web.HTTPBadGateway(text="official segment is not supported media")
        # URL query signatures can change even when the video has stopped.
        from urllib.parse import urlsplit
        seq = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
        marker = (seq[1] if seq else "") + ":" + urlsplit(url).path
        target = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
        stale_after = max(45, 3 * int(target[1] if target else 10))
        old = self.progress.get(program_id)
        now = time.monotonic()
        if old and old[0] == marker and now - old[1] > stale_after:
            raise web.HTTPBadGateway(text="official live playlist stopped advancing")
        if not old or old[0] != marker:
            self.progress[program_id] = (marker, now)

    async def check_channel(self, program_id: str, active: bool = False) -> None:
        async with (self.check_slots if active else self.idle_slots):
            try:
                await self.media_manifest(program_id, sample=True)
            except web.HTTPException:
                pass

    async def monitor(self) -> None:
        while True:
            now = time.monotonic()
            for pid in self.programs:
                current = self.check_tasks.get(pid)
                if current and not current.done():
                    continue
                if current:
                    # Retrieve unexpected failures so they do not silently kill
                    # monitoring or print credential-bearing task tracebacks.
                    if not current.cancelled() and current.exception():
                        self.health.setdefault(pid, {}).update(ok=False, lastError="monitor error",
                                                              checkedMonotonic=now)
                active = now - self.activity.get(pid, float("-inf")) < 90
                interval = self.active_interval if active else self.idle_interval
                last = self.health.get(pid, {}).get("checkedMonotonic", float("-inf"))
                if now - last >= interval:
                    # Do not queue the whole idle catalogue behind a semaphore:
                    # a channel that becomes active must have a free check lane.
                    count = sum(not task.done() and self.check_active.get(key, False) == active
                                for key, task in self.check_tasks.items())
                    if count >= (3 if active else 2):
                        continue
                    self.check_active[pid] = active
                    self.check_tasks[pid] = asyncio.create_task(self.check_channel(pid, active))
            await asyncio.sleep(1)

    async def index(self, request: web.Request) -> web.Response:
        self.require_access(request)
        pid = request.match_info["program_id"]
        if pid not in self.programs:
            raise web.HTTPNotFound(text="unknown official channel")
        self.activity[pid] = time.monotonic()
        # First use is media-verified rather than trusting an HTTP-200 manifest.
        state = self.health.get(pid, {})
        text, base = await self.media_manifest(pid, sample=not state.get("ok", False))
        body = self.rewrite_manifest(text, base, program_id=pid)
        return web.Response(text=body, content_type="application/vnd.apple.mpegurl", headers={
            "Cache-Control": "no-store", "X-Lumina-Upstream": "migu-official",
            "X-Lumina-Fetch-Location": "nas", "X-Lumina-Media-Mode": "nas-relay",
        })

    async def asset(self, request: web.Request) -> web.StreamResponse:
        payload = self.decode_asset_payload(request.match_info["token"])
        pid = payload.get("program")
        generation = self.media.get(pid)
        if pid in self.programs:
            self.activity[pid] = time.monotonic()
        try:
            return await super().asset(request)
        except MediaStreamInterrupted:
            if pid in self.programs:
                self.health.setdefault(pid, {}).update(ok=False, lastError="media interrupted")
                self.resolved.pop(pid, None)
                self.media.pop(pid, None)
            # The connection is closed. A second HTTP response would corrupt
            # the media body; the next index request performs verified repair.
            raise
        except ConnectionResetError:
            # A player changing channel is not an upstream failure.
            raise
        except (web.HTTPBadGateway, ClientError, OSError, TimeoutError) as exc:
            if pid in self.programs:
                # Refresh the channel but never substitute a different live
                # segment for the old sequence number (which corrupts A/V).
                await self.media_manifest(pid, failed_generation=generation[1] if generation else payload["url"])
            raise web.HTTPServiceUnavailable(text="reload channel playlist after media failure",
                                             headers={"Retry-After": "1"}) from exc

    async def official_playlist(self, request: web.Request) -> web.Response:
        self.require_access(request)
        # Relative entries keep API credentials on the same NAS/VPS endpoint.
        rows = ["#EXTM3U", "#PLAYLIST:咪咕官方（NAS回源）"]
        for pid, name in self.programs.items():
            state = self.health.get(pid, {})
            if not state.get("ok") or time.monotonic() - state.get("checkedMonotonic", 0) > self.idle_interval + 60:
                continue
            rows.extend([f'#EXTINF:-1 tvg-name="{name}" group-title="咪咕官方",{name}',
                         f'/api/migu/{pid}/index.m3u8?{urlencode({"access_token": self.access_token})}'])
        if len(rows) == 2:
            raise web.HTTPServiceUnavailable(text="official channel checks not ready")
        return web.Response(text="\n".join(rows) + "\n", content_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-store"})
