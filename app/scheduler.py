from __future__ import annotations

import json
import logging
import time

from . import settings
from .resolver import atomic_write, refresh, utc_now


logging.basicConfig(
    level=getattr(logging, __import__("os").environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("lumina.scheduler")


def run_once() -> None:
    previous = {}
    try:
        previous = json.loads(settings.STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    refreshing = {
        **previous,
        "ok": bool(previous.get("ok")),
        "state": "refreshing",
        "refreshing": True,
        "refreshStartedAt": utc_now(),
    }
    atomic_write(settings.STATUS_PATH, json.dumps(refreshing, ensure_ascii=False, indent=2) + "\n")
    try:
        status = refresh()
        status["state"] = "ready"
        status["refreshing"] = False
        atomic_write(settings.STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
        LOG.info("refresh complete: %s", json.dumps(status, ensure_ascii=False))
    except Exception as exc:  # scheduler must preserve the last good playlist
        LOG.exception("refresh failed")
        status = {
            "ok": False,
            "updatedAt": utc_now(),
            "error": str(exc)[:500],
            "keptPreviousPlaylist": settings.PLAYLIST_PATH.exists(),
            "state": "error",
            "refreshing": False,
            "channels": previous.get("channels", {}),
            "published": previous.get("published", 0),
            "requested": previous.get("requested", 0),
        }
        atomic_write(settings.STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    time.sleep(settings.STARTUP_DELAY)
    while True:
        started = time.monotonic()
        run_once()
        elapsed = time.monotonic() - started
        deadline = time.monotonic() + max(60, settings.REFRESH_INTERVAL - elapsed)
        while time.monotonic() < deadline:
            if settings.REFRESH_REQUEST_PATH.exists():
                try:
                    settings.REFRESH_REQUEST_PATH.unlink()
                except OSError:
                    pass
                break
            time.sleep(min(2, max(0.1, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
