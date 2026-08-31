from __future__ import annotations

import json
import logging
import mimetypes
import time
from pathlib import Path

from aiohttp import web

from . import settings


STARTED = time.monotonic()
ALLOWED_PLAYLISTS = {"yangshi.m3u", "playlist.m3u"}


def common_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    }


def playlist_ready() -> bool:
    try:
        text = settings.PLAYLIST_PATH.read_text(encoding="utf-8-sig")
        return text.startswith("#EXTM3U") and "#EXTINF:" in text
    except OSError:
        return False


async def root(_: web.Request) -> web.Response:
    return web.json_response({
        "name": "LuminaLive NAS",
        "playlist": "/live/yangshi.m3u",
        "health": "/healthz",
        "status": "/status.json",
    }, headers=common_headers())


async def health(_: web.Request) -> web.Response:
    ready = playlist_ready()
    return web.json_response({
        "ok": ready,
        "uptimeSeconds": round(time.monotonic() - STARTED, 1),
        "playlistReady": ready,
    }, status=200 if ready else 503, headers=common_headers())


async def status(_: web.Request) -> web.Response:
    try:
        payload = json.loads(settings.STATUS_PATH.read_text(encoding="utf-8"))
        return web.json_response(payload, headers=common_headers())
    except (OSError, json.JSONDecodeError):
        return web.json_response({"ok": False, "state": "starting"}, status=503, headers=common_headers())


async def playlist(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    if filename not in ALLOWED_PLAYLISTS:
        raise web.HTTPNotFound()
    try:
        body = settings.PLAYLIST_PATH.read_bytes()
    except OSError as exc:
        raise web.HTTPServiceUnavailable(text="playlist is being generated; retry later") from exc
    return web.Response(
        body=body,
        content_type="application/vnd.apple.mpegurl",
        charset="utf-8",
        headers=common_headers(),
    )


def create_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", root)
    app.router.add_get("/healthz", health)
    app.router.add_get("/status.json", status)
    app.router.add_get("/live/{filename}", playlist)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host=settings.HOST, port=settings.PORT, access_log=None)


if __name__ == "__main__":
    main()

