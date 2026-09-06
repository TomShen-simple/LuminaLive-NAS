"""Official-only NAS service. Never starts the public-M3U scheduler."""
from __future__ import annotations

import logging
import os

from aiohttp import web

from .official_migu import MonitoredMiguRelay


def create_app() -> web.Application:
    relay = MonitoredMiguRelay()
    if not relay.enabled:
        raise RuntimeError("Official relay requires a token and signing secret")
    app = web.Application(client_max_size=1024 * 1024)
    app["migu_relay"] = relay
    app.on_startup.append(relay.start)
    app.on_cleanup.append(relay.stop)

    async def info(request: web.Request) -> web.Response:
        return web.json_response({"service": "official-only", "provider": "migu",
                                  "fetchLocation": "nas", "publicM3uSources": False},
                                 headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/info", info)
    # Liveness is deliberately distinct from authenticated per-channel health.
    app.router.add_get("/healthz", info)
    app.router.add_get("/api/migu/status", relay.status_response)
    app.router.add_get("/api/migu/playlist.m3u", relay.official_playlist)
    app.router.add_get("/api/migu/{program_id}/index.m3u8", relay.index)
    app.router.add_route("GET", "/api/migu/asset/{token}", relay.asset)
    app.router.add_route("HEAD", "/api/migu/asset/{token}", relay.asset)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    web.run_app(create_app(), host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8780")), access_log=None)
