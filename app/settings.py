from __future__ import annotations

import os
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CHANNELS_PATH = CONFIG_DIR / "channels.json"
LOCAL_M3U_PATH = CONFIG_DIR / "local.m3u"
PLAYLIST_PATH = DATA_DIR / "yangshi.m3u"
STATUS_PATH = DATA_DIR / "status.json"
BACKUP_DIR = DATA_DIR / "backups"
HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVER_PORT", "8780"))
REFRESH_INTERVAL = max(300, int(os.environ.get("REFRESH_INTERVAL", "1800")))
STARTUP_DELAY = max(0, int(os.environ.get("STARTUP_DELAY", "3")))
HTTP_TIMEOUT = max(3, int(os.environ.get("HTTP_TIMEOUT", "10")))
CHANNEL_WORKERS = max(1, min(64, int(os.environ.get("CHANNEL_WORKERS", "24"))))
MAX_CANDIDATES = max(1, min(20, int(os.environ.get("MAX_CANDIDATES_PER_CHANNEL", "8"))))
MAX_BANDWIDTH = max(500_000, int(os.environ.get("MAX_BANDWIDTH", "10000000")))
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_UPSTREAMS", "false").lower() in {
    "1", "true", "yes", "on"
}
EXTRA_M3U_URLS = tuple(
    value.strip()
    for value in os.environ.get("EXTRA_M3U_URLS", "").split(",")
    if value.strip()
)
USER_AGENT = os.environ.get(
    "UPSTREAM_USER_AGENT",
    "Mozilla/5.0 (Linux; Android 14; TV) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
)

