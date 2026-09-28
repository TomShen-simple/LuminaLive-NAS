"""Read-only diagnostic: can the VPS fetch NAS-signed Migu media directly?"""
from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


manifest_url = "http://127.0.0.1:8783/live-auto/cctv5/index.m3u8"
with urlopen(manifest_url, timeout=20) as response:
    playlist = response.read(1_000_001).decode("utf-8-sig")
assets = [line.strip() for line in playlist.splitlines() if line.startswith("/live-auto/cctv5/api/migu/asset/")]
if not assets:
    raise RuntimeError("no official CCTV5 media")
token = assets[-1].rsplit("/", 1)[-1].split(".", 1)[0]
payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
url = payload.get("url", "")
host = (urlsplit(url).hostname or "").lower()
if urlsplit(url).scheme not in {"http", "https"} or not host or not (
    host == "miguvideo.com" or host.endswith(".miguvideo.com")
):
    print(json.dumps({"directCdnAllowed": False, "cdnHost": host,
                      "scheme": urlsplit(url).scheme}))
    raise SystemExit(0)
headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
    "Referer": "https://m.miguvideo.com/",
    "Range": "bytes=0-65535",
}
for scheme in (urlsplit(url).scheme, "https"):
    candidate = urlunsplit(urlsplit(url)._replace(scheme=scheme))
    started = time.monotonic()
    try:
        with urlopen(Request(candidate, headers=headers), timeout=15) as response:
            prefix = response.read(65536)
            status = response.status
        print(json.dumps({"directCdnAllowed": True, "cdnHost": host, "scheme": scheme,
                          "status": status, "bytes": len(prefix),
                          "seconds": round(time.monotonic() - started, 2),
                          "video": len(prefix) > 188 and prefix[0] == prefix[188] == 0x47}))
    except Exception as exc:
        print(json.dumps({"directCdnAllowed": True, "cdnHost": host, "scheme": scheme,
                          "error": type(exc).__name__, "status": getattr(exc, "code", None),
                          "seconds": round(time.monotonic() - started, 2)}))
