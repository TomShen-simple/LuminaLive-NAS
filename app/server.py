from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from . import admin, settings
from .migu_relay import MiguRelay


STARTED = time.monotonic()
ALLOWED_PLAYLISTS = {"yangshi.m3u", "playlist.m3u"}
WEB_DIR = Path(__file__).with_name("web")


def common_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def playlist_headers() -> dict[str, str]:
    return common_headers()


def playlist_ready() -> bool:
    try:
        text = settings.PLAYLIST_PATH.read_text(encoding="utf-8-sig")
        return text.startswith("#EXTM3U") and "#EXTINF:" in text
    except OSError:
        return False


def client_is_private(request: web.Request) -> bool:
    try:
        return ipaddress.ip_address(request.remote or "").is_private
    except ValueError:
        return False


def require_admin(request: web.Request) -> None:
    if request.headers.get("X-Lumina-Admin") != "1":
        raise web.HTTPForbidden(text="missing admin request header")
    if settings.ADMIN_TOKEN:
        supplied = request.headers.get("X-Lumina-Token", "")
        if not hmac.compare_digest(supplied, settings.ADMIN_TOKEN):
            raise web.HTTPUnauthorized(text="invalid admin token")
    elif not client_is_private(request):
        raise web.HTTPForbidden(text="admin writes are limited to private networks")


async def root(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html", headers={
        **common_headers(),
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
    })


async def static_asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in {"app.css", "overrides.css", "app.js"}:
        raise web.HTTPNotFound()
    return web.FileResponse(WEB_DIR / name, headers=common_headers())


async def info(_: web.Request) -> web.Response:
    return web.json_response({
        "name": "LuminaLive NAS",
        "dashboard": "/",
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
    payload = admin.read_status()
    if payload:
        return web.json_response(payload, headers=common_headers())
    return web.json_response({"ok": False, "state": "starting"}, status=503, headers=common_headers())


async def dashboard(_: web.Request) -> web.Response:
    try:
        return web.json_response(admin.dashboard(), headers=common_headers())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=503, headers=common_headers())


async def get_local_m3u(request: web.Request) -> web.Response:
    require_admin(request)
    return web.json_response(admin.read_local_m3u(), headers=common_headers())


async def put_local_m3u(request: web.Request) -> web.Response:
    require_admin(request)
    try:
        payload = await request.json()
        result = admin.save_local_m3u(str(payload.get("content", "")))
        return web.json_response(result, headers=common_headers())
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400, headers=common_headers())


async def delete_local_m3u(request: web.Request) -> web.Response:
    require_admin(request)
    existed = settings.LOCAL_M3U_PATH.exists()
    try:
        settings.LOCAL_M3U_PATH.unlink(missing_ok=True)
    except OSError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500, headers=common_headers())
    return web.json_response({"ok": True, "deleted": existed}, headers=common_headers())


async def put_sources(request: web.Request) -> web.Response:
    require_admin(request)
    try:
        payload = await request.json()
        return web.json_response(admin.save_sources(payload.get("sources")), headers=common_headers())
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400, headers=common_headers())


async def refresh_now(request: web.Request) -> web.Response:
    require_admin(request)
    return web.json_response(admin.request_refresh(), status=202, headers=common_headers())


async def playlist(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    if filename not in ALLOWED_PLAYLISTS:
        raise web.HTTPNotFound()
    try:
        body = settings.PLAYLIST_PATH.read_bytes()
    except OSError as exc:
        raise web.HTTPServiceUnavailable(text="playlist is being generated; retry later") from exc
    return web.Response(body=body, content_type="application/vnd.apple.mpegurl", charset="utf-8", headers=playlist_headers())


def create_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    migu = MiguRelay()
    app["migu_relay"] = migu
    app.on_startup.append(migu.start)
    app.on_cleanup.append(migu.stop)
    app.router.add_get("/", root)
    app.router.add_get("/assets/{name}", static_asset)
    app.router.add_get("/api/info", info)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/local-m3u", get_local_m3u)
    app.router.add_put("/api/local-m3u", put_local_m3u)
    app.router.add_delete("/api/local-m3u", delete_local_m3u)
    app.router.add_put("/api/source-playlists", put_sources)
    app.router.add_post("/api/refresh", refresh_now)
    app.router.add_get("/healthz", health)
    app.router.add_get("/status.json", status)
    app.router.add_get("/live/{filename}", playlist)
    app.router.add_get("/api/migu/{program_id}/index.m3u8", migu.index)
    app.router.add_route("*", "/api/migu/asset/{token}", migu.asset)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host=settings.HOST, port=settings.PORT, access_log=None)


if __name__ == "__main__":
    main()
