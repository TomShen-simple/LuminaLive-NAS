"""VPS gateway for the NAS official-only relay; no third-party fallback."""
from __future__ import annotations

import hmac
import os
import re
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

URI_RE = re.compile(r'URI="([^"]+)"')
PATH_RE = re.compile(r"/api/migu/(?:[0-9]{4,32}/index\.m3u8|asset/[A-Za-z0-9_.-]+)$")


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
        if line.startswith("#"):
            line = URI_RE.sub(lambda m: 'URI="' + rewrite(m[1]) + '"', line)
        elif line.strip():
            line = rewrite(line.strip())
        lines.append(line)
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

    async def start(app):
        app["http"] = ClientSession(timeout=ClientTimeout(total=None, connect=5, sock_read=35),
                                    auto_decompress=False)

    async def stop(app):
        await app["http"].close()

    async def serve(request):
        if not hmac.compare_digest(request.match_info["key"], playback_key):
            raise web.HTTPNotFound()
        tail = request.match_info["tail"]
        if tail == "playlist.m3u":
            upstream_path = "/api/migu/playlist.m3u"
        elif PATH_RE.fullmatch("/" + tail):
            upstream_path = "/" + tail
        else:
            raise web.HTTPNotFound()
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
                    text = rewrite_playlist(data.decode("utf-8-sig"), public_base + "/" + playback_key)
                    return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers=common)
                common.update({k: v for k, v in response.headers.items() if k.lower() in {
                    "content-type", "content-length", "content-range", "accept-ranges"}})
                downstream = web.StreamResponse(status=response.status, headers=common)
                await downstream.prepare(request)
                if request.method != "HEAD":
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
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=int(os.environ.get("OFFICIAL_PORT", "8783")),
                access_log=None)
