"""Read-only VPS -> NAS -> Migu audit; never logs signed URLs or tokens."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE = os.environ.get("AUDIT_RELAY_BASE", "http://127.0.0.1:18786")
TOKEN = os.environ["CCTV_MIGU_RELAY_TOKEN"]
CHANNELS = {
    "CCTV1": "608807420", "CCTV5": "641886683", "CCTV5+": "641886773",
    "CCTV10": "624878405", "CCTV11": "667987558", "东方卫视": "651632648",
}


def manifest(url):
    req = Request(url, headers={"Authorization": "Bearer " + TOKEN})
    with urlopen(req, timeout=12) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("manifest too large")
    text = data.decode("utf-8-sig")
    if not text.startswith("#EXTM3U"):
        raise ValueError("not HLS")
    return text


def audit(item):
    name, pid = item
    started = time.monotonic()
    stage = "index"
    try:
        root = BASE + "/api/migu/" + pid + "/index.m3u8"
        url = root
        text = manifest(url)
        for _ in range(3):
            if "#EXTINF:" in text:
                break
            line = next(x for x in text.splitlines() if x and not x.startswith("#"))
            url = urljoin(url, line)
            stage = "media_playlist"
            text = manifest(url)
        elapsed = round(time.monotonic() - started, 2)
        duration = 0.0
        segments = []
        for line in text.splitlines():
            if line.startswith("#EXTINF:"):
                duration = float(line.split(":")[1].split(",")[0])
            elif line and not line.startswith("#"):
                segments.append((urljoin(url, line), duration))
        results = []
        for segment, duration in segments[-2:]:
            stage = "segment"
            st = time.monotonic()
            with urlopen(segment, timeout=12) as response:
                data = response.read(8_000_001)
            seconds = time.monotonic() - st
            results.append({"bytes": len(data), "complete": len(data) <= 8_000_000,
                            "downloadSeconds": round(seconds, 2), "durationSeconds": duration,
                            "mediaSecondsPerDownloadSecond": round(duration / seconds, 2),
                            "mpegts": len(data) > 188 and data[0] == data[188] == 0x47})
        seq = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
        return {"channel": name, "manifestSeconds": elapsed,
                "sequence": int(seq[1]) if seq else None, "segments": results}
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            body = exc.read(256).decode(errors="replace")
            match = re.search(r"Migu (?:API [A-Za-z0-9_-]+|asset HTTP [0-9]+|manifest request failed|index request failed)", body)
            detail = match[0] if match else ""
        return {"channel": name, "error": type(exc).__name__,
                "httpStatus": getattr(exc, "code", None), "stage": stage, "detail": detail}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        for result in executor.map(audit, CHANNELS.items()):
            print(json.dumps(result, ensure_ascii=False), flush=True)
