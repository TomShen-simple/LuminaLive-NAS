"""VPS gateway for the NAS official-only relay; no third-party fallback."""
from __future__ import annotations

import hmac
import base64
import binascii
import json
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

URI_RE = re.compile(r'URI="([^"]+)"')
PATH_RE = re.compile(r"/api/migu/(?:[0-9]{4,32}/index\.m3u8|asset/[A-Za-z0-9_.-]+)$")
INDEX_RE = re.compile(r"/api/migu/([0-9]{4,32})/index\.m3u8$")
AD_MARKERS = ("#EXT-X-CUE-OUT", "#EXT-X-SCTE35", "#EXT-OATCLS-SCTE35", "SCTE35-OUT", 'CLASS="AD', 'CLASS="com.apple.hls.interstitial')
# Keep the legacy public CCTV URLs on the same guarded NAS -> VPS path.
# CCTV16 has no Migu official programme in our catalogue and must not be
# silently substituted with a third-party stream.
LEGACY_CCTV_PROGRAMS = {
    "cctv1": "608807420", "cctv2": "631780532", "cctv3": "624878271",
    "cctv4": "631780421", "cctv5": "641886683", "cctv5plus": "641886773",
    "cctv6": "624878396", "cctv7": "673168121", "cctv8": "624878356",
    "cctv9": "673168140", "cctv10": "624878405", "cctv11": "667987558",
    "cctv12": "673168185", "cctv13": "608807423", "cctv14": "624878440",
    "cctv15": "673168223", "cctv17": "673168256",
}


def media_assets(text: str) -> list[str]:
    """Fail closed on malformed or explicitly ad-marked live HLS."""
    if not text.startswith("#EXTM3U") or "#EXT-X-ENDLIST" in text:
        raise ValueError("not a live HLS playlist")
    if any(marker.lower() in text.lower() for marker in AD_MARKERS):
        raise ValueError("ad insertion marker")
    if "#EXT-X-KEY:" in text or "#EXT-X-SESSION-KEY:" in text:
        raise ValueError("encrypted media cannot be inspected")
    if not re.search(r"(?m)^#EXT-X-TARGETDURATION:[1-9][0-9]*\s*$", text):
        raise ValueError("missing target duration")
    if not re.search(r"(?m)^#EXT-X-MEDIA-SEQUENCE:[0-9]+\s*$", text):
        raise ValueError("missing live sequence")
    assets = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if "#EXTINF:" not in text or not assets or len(assets) > 200:
        raise ValueError("missing media segments")
    for asset in assets:
        if not re.fullmatch(r"/api/migu/asset/[A-Za-z0-9_.-]+", urlsplit(asset).path):
            raise ValueError("unexpected media path")
    return [urlsplit(asset).path for asset in assets]


def media_prefix_ok(data: bytes) -> bool:
    if len(data) >= 376 and any(data[i] == data[i + 188] == 0x47
                                for i in range(min(188, len(data) - 188))):
        return True
    return len(data) >= 12 and data[4:8] in {b"ftyp", b"styp", b"moof", b"sidx"}


async def read_prefix(content, limit: int = 4096) -> bytes:
    data = bytearray()
    while len(data) < limit:
        chunk = await content.read(limit - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def asset_program(path: str) -> str | None:
    try:
        token = path.rsplit("/", 1)[1].split(".", 1)[0]
        if len(token) > 4096:
            return None
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        pid = str(payload.get("program", ""))
        return pid if re.fullmatch(r"[0-9]{4,32}", pid) else None
    except (ValueError, TypeError, binascii.Error):
        return None


class GatewayGuard:
    def __init__(self) -> None:
        self.approved: dict[str, dict[str, float]] = {}
        self.checked_until: dict[str, float] = {}
        self.last_result: dict[str, dict] = {}

    def authorized(self, path: str) -> bool:
        pid = asset_program(path)
        return bool(pid and self.approved.get(pid, {}).get(path, 0) > time.monotonic())

    async def inspect(self, pid: str, text: str, http: ClientSession,
                      relay_base: str, headers: dict[str, str]) -> None:
        try:
            assets = media_assets(text)
            if any(asset_program(path) != pid for path in assets):
                raise ValueError("media belongs to another channel")
            now = time.monotonic()
            if now >= self.checked_until.get(pid, 0):
                sample_headers = {**headers, "Range": "bytes=0-4095"}
                async with http.get(relay_base + assets[-1], headers=sample_headers,
                                    allow_redirects=False, timeout=ClientTimeout(total=8)) as response:
                    if response.status not in (200, 206):
                        raise ValueError("NAS sample unavailable")
                    sample = await read_prefix(response.content)
                if not media_prefix_ok(sample):
                    raise ValueError("NAS sample is not video")
                self.checked_until[pid] = time.monotonic() + 15
            expiry = time.monotonic() + 150
            known = {path: until for path, until in self.approved.get(pid, {}).items() if until > time.monotonic()}
            known.update({path: expiry for path in assets})
            self.approved[pid] = known
            self.last_result[pid] = {"ok": True, "checkedAt": time.time(),
                                     "reason": "HLS structure and video sample passed"}
        except (ClientError, OSError, TimeoutError, ValueError) as exc:
            self.approved.pop(pid, None)
            self.checked_until.pop(pid, None)
            self.last_result[pid] = {"ok": False, "checkedAt": time.time(), "reason": str(exc) if isinstance(exc, ValueError) else "sample unavailable"}
            raise web.HTTPServiceUnavailable(text="official channel verification failed", headers={"Retry-After": "3"}) from None


def rewrite_playlist(text: str, prefix: str) -> str:
    if not text.startswith("#EXTM3U"):
        raise ValueError("not an HLS/M3U playlist")

    def rewrite(value: str) -> str:
        parsed = urlsplit(value)
        # NAS emits local signed paths. Never leak its bearer token or turn
        # an unexpected URL into a public open proxy.
        if parsed.scheme or parsed.netloc or not PATH_RE.fullmatch(parsed.path):
            raise ValueError("unexpected NAS playlist URL")
        return prefix.rstrip("/") + parsed.path

    lines = []
    for line in text.splitlines():
        if "#EXT-X-TARGETDURATION:" not in text:
            if line == "#EXTM3U":
                line = '#EXTM3U x-tvg-url="https://pair.simpleboy.de/live/yangshi.xml"'
            elif line.startswith("#EXTINF:") and 'tvg-id="' not in line:
                match = re.search(r'tvg-name="([^"]+)"', line)
                if match:
                    channel_id = "CCTV5PLUS" if match[1] == "CCTV5+" else match[1]
                    line = line.replace('tvg-name=', f'tvg-id="{channel_id}" tvg-name=', 1)
        if line.startswith("#"):
            line = URI_RE.sub(lambda m: 'URI="' + rewrite(m[1]) + '"', line)
        elif line.strip():
            line = rewrite(line.strip())
        lines.append(line)
    # Hint live players to start three target durations behind the live edge.
    # Keep every segment and sequence intact for clients already playing.
    target = re.search(r"(?m)^#EXT-X-TARGETDURATION:(\d+)\s*$", text)
    if target and "#EXTINF:" in text and "#EXT-X-ENDLIST" not in text and "#EXT-X-START:" not in text:
        duration = int(target[1])
        if duration > 0:
            lines.insert(1, f"#EXT-X-START:TIME-OFFSET=-{3 * duration},PRECISE=NO")
    return "\n".join(lines) + "\n"


def create_app() -> web.Application:
    relay_base = os.environ.get("OFFICIAL_RELAY_BASE", os.environ["CCTV_MIGU_RELAY_BASE"])
    relay_parts = urlsplit(relay_base)
    # The legacy resolver environment points at /api/migu, while the official
    # gateway appends that path itself. Accept both forms so a VPS upgrade does
    # not silently produce /api/migu/api/migu/... requests.
    if relay_parts.path.rstrip("/") == "/api/migu":
        relay_base = urlunsplit((relay_parts.scheme, relay_parts.netloc, "", relay_parts.query, relay_parts.fragment))
    relay_base = relay_base.rstrip("/")
    relay_token = os.environ["CCTV_MIGU_RELAY_TOKEN"]
    playback_key = os.environ["OFFICIAL_PLAYBACK_KEY"]
    public_base = os.environ["OFFICIAL_PUBLIC_BASE"].rstrip("/")
    if len(playback_key) < 24:
        raise ValueError("playback key is too short")
    if urlsplit(relay_base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("NAS relay must use a local reverse tunnel")
    app = web.Application()
    guard = GatewayGuard()

    async def start(app):
        app["http"] = ClientSession(timeout=ClientTimeout(total=None, connect=5, sock_read=35),
                                    auto_decompress=False)

    async def stop(app):
        await app["http"].close()

    async def serve(request):
        legacy_channel = request.match_info.get("channel", "").lower()
        legacy_pid = LEGACY_CCTV_PROGRAMS.get(legacy_channel)
        if "channel" in request.match_info:
            if not legacy_pid:
                raise web.HTTPNotFound()
        elif not hmac.compare_digest(request.match_info["key"], playback_key):
            raise web.HTTPNotFound()
        tail = request.match_info["tail"]
        if tail == "guard.json" and not legacy_pid:
            return web.json_response({"channels": guard.last_result}, headers={"Cache-Control": "no-store"})
        if legacy_pid and tail == "index.m3u8":
            upstream_path = f"/api/migu/{legacy_pid}/index.m3u8"
        elif tail == "playlist.m3u" and not legacy_pid:
            upstream_path = "/api/migu/playlist.m3u"
        elif PATH_RE.fullmatch("/" + tail):
            upstream_path = "/" + tail
        else:
            raise web.HTTPNotFound()
        if legacy_pid and upstream_path != f"/api/migu/{legacy_pid}/index.m3u8" and (
            not upstream_path.startswith("/api/migu/asset/") or asset_program(upstream_path) != legacy_pid
        ):
            raise web.HTTPNotFound()
        index_match = INDEX_RE.fullmatch(upstream_path)
        if "/asset/" in upstream_path and not guard.authorized(upstream_path):
            raise web.HTTPServiceUnavailable(text="media has not passed channel verification",
                                             headers={"Retry-After": "3"})
        headers = {"Authorization": "Bearer " + relay_token, "Accept-Encoding": "identity"}
        if "Range" in request.headers and "/asset/" in upstream_path:
            headers["Range"] = request.headers["Range"]
        downstream = None
        try:
            async with app["http"].get(relay_base + upstream_path, headers=headers,
                                       allow_redirects=False) as response:
                if response.status not in (200, 206):
                    raise web.HTTPServiceUnavailable(text="official source temporarily unavailable",
                                                     headers={"Retry-After": "2"})
                common = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                          "X-Lumina-Upstream": "migu-official", "X-Lumina-Fetch-Location": "nas",
                          "X-Lumina-Media-Mode": "nas-vps-relay"}
                if upstream_path.endswith((".m3u", ".m3u8")):
                    data = bytearray()
                    async for part in response.content.iter_chunked(65536):
                        data.extend(part)
                        if len(data) > 1_000_000:
                            break
                    if len(data) > 1_000_000:
                        raise ValueError("playlist too large")
                    source_text = data.decode("utf-8-sig")
                    if index_match:
                        await guard.inspect(index_match[1], source_text, app["http"], relay_base, headers)
                    prefix = ("/live-auto/" + legacy_channel) if legacy_pid else (public_base + "/" + playback_key)
                    text = rewrite_playlist(source_text, prefix)
                    return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers=common)
                # Validate each media body before sending response headers. A
                # NAS error page with HTTP 200 must never reach the player.
                prefix = b""
                range_header = request.headers.get("Range", "")
                is_range_middle = bool(range_header and not range_header.startswith("bytes=0-"))
                if request.method != "HEAD" and "/asset/" in upstream_path and not is_range_middle:
                    prefix = await read_prefix(response.content)
                    if not media_prefix_ok(prefix):
                        pid = asset_program(upstream_path)
                        if pid:
                            guard.approved.pop(pid, None)
                            guard.checked_until.pop(pid, None)
                            guard.last_result[pid] = {"ok": False, "checkedAt": time.time(),
                                                      "reason": "media is not video"}
                        raise web.HTTPServiceUnavailable(text="official media verification failed",
                                                         headers={"Retry-After": "3"})
                common.update({k: v for k, v in response.headers.items() if k.lower() in {
                    "content-type", "content-length", "content-range", "accept-ranges"}})
                downstream = web.StreamResponse(status=response.status, headers=common)
                await downstream.prepare(request)
                if request.method != "HEAD":
                    if prefix:
                        await downstream.write(prefix)
                    async for chunk in response.content.iter_chunked(128 * 1024):
                        await downstream.write(chunk)
                await downstream.write_eof()
                return downstream
        except (ClientError, OSError, ValueError, TimeoutError) as exc:
            if downstream is not None and downstream.prepared:
                if request.transport is not None:
                    request.transport.close()
                raise ConnectionResetError("official media interrupted") from None
            raise web.HTTPServiceUnavailable(text="official relay temporarily unavailable",
                                             headers={"Retry-After": "2"}) from None

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.router.add_get("/live-official/{key}/{tail:.*}", serve)
    app.router.add_get("/live-auto/{channel}/{tail:.*}", serve)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=int(os.environ.get("OFFICIAL_PORT", "8783")),
                access_log=None)
