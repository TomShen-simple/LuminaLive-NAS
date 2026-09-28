"""Compare complete NAS and public-gateway live segments without logging URLs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def env_file(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key] = value.strip("\"'")
    return result


config = {**env_file("/etc/lumina-resolver.env"),
          **env_file("/etc/lumina-official-gateway.env")}
token = config["CCTV_MIGU_RELAY_TOKEN"]
headers = {"Authorization": "Bearer " + token}


def fetch(url, auth=False):
    started = time.monotonic()
    try:
        with urlopen(Request(url, headers=headers if auth else {}), timeout=20) as response:
            data = response.read(12_000_001)
            return {"status": response.status, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "seconds": round(time.monotonic() - started, 2),
                    "tooLarge": len(data) > 12_000_000}
    except Exception as exc:
        return {"error": type(exc).__name__,
                "status": getattr(exc, "code", None),
                "seconds": round(time.monotonic() - started, 2)}


seen = set()
deadline = time.monotonic() + 150
with ThreadPoolExecutor(max_workers=2) as pool:
    while len(seen) < 10 and time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8783/live-auto/cctv5/index.m3u8", timeout=20) as response:
                playlist = response.read().decode("utf-8-sig")
            assets = [urlsplit(line).path for line in playlist.splitlines()
                      if line and not line.startswith("#")]
            asset = assets[-1]
            sequence = re.search(r"(?m)^#EXT-X-MEDIA-SEQUENCE:(\d+)", playlist)
            marker = (sequence[1] if sequence else "") + ":" + str(len(assets))
            if marker in seen:
                time.sleep(2)
                continue
            seen.add(marker)
            nas_asset = "/api/migu/asset/" + asset.split("/api/migu/asset/", 1)[1]
            nas = pool.submit(fetch, "http://127.0.0.1:18786" + nas_asset, True)
            vps = pool.submit(fetch, "http://127.0.0.1:8783" + asset)
            left, right = nas.result(), vps.result()
            print(json.dumps({"sample": len(seen), "nas": {k: v for k, v in left.items() if k != "sha256"},
                              "vps": {k: v for k, v in right.items() if k != "sha256"},
                              "identical": left.get("sha256") == right.get("sha256")
                              if "sha256" in left and "sha256" in right else None}), flush=True)
        except Exception as exc:
            print(json.dumps({"sample": len(seen) + 1, "error": type(exc).__name__,
                              "status": getattr(exc, "code", None)}), flush=True)
            time.sleep(2)
