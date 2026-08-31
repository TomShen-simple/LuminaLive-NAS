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
    try:
        status = refresh()
        LOG.info("refresh complete: %s", json.dumps(status, ensure_ascii=False))
    except Exception as exc:  # scheduler must preserve the last good playlist
        LOG.exception("refresh failed")
        status = {
            "ok": False,
            "updatedAt": utc_now(),
            "error": str(exc)[:500],
            "keptPreviousPlaylist": settings.PLAYLIST_PATH.exists(),
        }
        atomic_write(settings.STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    time.sleep(settings.STARTUP_DELAY)
    while True:
        started = time.monotonic()
        run_once()
        elapsed = time.monotonic() - started
        time.sleep(max(60, settings.REFRESH_INTERVAL - elapsed))


if __name__ == "__main__":
    main()

