from __future__ import annotations

import json
import math
from urllib.parse import urlparse

from . import settings
from .resolver import ATTR_RE, atomic_write, load_config, requested_channels, utc_now


MAX_M3U_BYTES = 1_000_000
MAX_SOURCES = 25


def read_status() -> dict:
    try:
        value = json.loads(settings.STATUS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def config_records(config: dict) -> list[dict]:
    _, ordered = requested_channels(config)
    by_id = {}
    for group in config["groups"]:
        group_name = str(group.get("title") or "其他")
        for item in group.get("channels", []):
            channel_id = str(item.get("id") or item["name"])
            by_id[channel_id] = {
                "id": channel_id,
                "name": str(item["name"]),
                "group": group_name,
            }
    return [by_id[channel_id] for _, channel_id in ordered if channel_id in by_id]


def percentile(values: list[int], percentage: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentage) - 1)
    return ordered[index]


def dashboard() -> dict:
    config = load_config(settings.CHANNELS_PATH)
    status = read_status()
    current = status.get("channels") if isinstance(status.get("channels"), dict) else {}
    channels = []
    latencies = []
    for record in config_records(config):
        checked = current.get(record["id"], {})
        latency = checked.get("validationMs") if isinstance(checked, dict) else None
        if isinstance(latency, int):
            latencies.append(latency)
        channels.append({
            **record,
            "online": isinstance(latency, int),
            "validationMs": latency,
            "upstreamHost": checked.get("upstreamHost") if isinstance(checked, dict) else None,
        })
    requested = len(channels)
    published = sum(1 for item in channels if item["online"])
    return {
        "ok": bool(status.get("ok")) and published > 0,
        "state": status.get("state") or ("ready" if published else "starting"),
        "refreshing": bool(status.get("refreshing")),
        "updatedAt": status.get("updatedAt"),
        "elapsedSeconds": status.get("elapsedSeconds"),
        "published": published,
        "requested": requested,
        "healthPercent": round(published * 100 / requested) if requested else 0,
        "averageValidationMs": round(sum(latencies) / len(latencies)) if latencies else None,
        "p95ValidationMs": percentile(latencies, 0.95),
        "channels": channels,
        "sources": {
            "configured": [str(value) for value in config.get("source_playlists", [])],
            "environment": list(settings.EXTRA_M3U_URLS),
            "localExists": settings.LOCAL_M3U_PATH.exists(),
        },
        "authRequired": bool(settings.ADMIN_TOKEN),
        "error": status.get("error"),
    }


def parse_local_m3u(text: str) -> list[dict]:
    entries = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF"):
            pending = line
            continue
        if not pending or not line.startswith(("http://", "https://")):
            continue
        attrs = dict(ATTR_RE.findall(pending))
        label = pending.rsplit(",", 1)[-1].strip()
        entries.append({
            "name": attrs.get("tvg-name") or label,
            "group": attrs.get("group-title") or "自定义",
            "url": line,
        })
        pending = ""
    return entries


def read_local_m3u() -> dict:
    try:
        content = settings.LOCAL_M3U_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        content = "#EXTM3U\n"
    return {"content": content, "entries": parse_local_m3u(content)}


def valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def validate_m3u(content: str) -> list[dict]:
    if len(content.encode("utf-8")) > MAX_M3U_BYTES:
        raise ValueError("M3U 不能超过 1 MB")
    if not content.lstrip("\ufeff\r\n ").startswith("#EXTM3U"):
        raise ValueError("内容必须以 #EXTM3U 开头")
    entries = parse_local_m3u(content)
    invalid = [item["url"] for item in entries if not valid_http_url(item["url"])]
    if invalid:
        raise ValueError("M3U 包含无效的 HTTP/HTTPS 地址")
    return entries


def save_local_m3u(content: str) -> dict:
    entries = validate_m3u(content)
    normalized = content.replace("\r\n", "\n").rstrip() + "\n"
    changed = atomic_write(settings.LOCAL_M3U_PATH, normalized)
    return {"ok": True, "changed": changed, "entries": entries}


def validate_sources(values: object) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_SOURCES:
        raise ValueError(f"远程 M3U 必须是列表且最多 {MAX_SOURCES} 条")
    result = []
    for raw in values:
        value = str(raw).strip()
        if not valid_http_url(value):
            raise ValueError(f"无效的 M3U 地址：{value[:120]}")
        if value not in result:
            result.append(value)
    return result


def save_sources(values: object) -> dict:
    sources = validate_sources(values)
    config = load_config(settings.CHANNELS_PATH)
    config["source_playlists"] = sources
    changed = atomic_write(
        settings.CHANNELS_PATH,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    )
    return {"ok": True, "changed": changed, "sources": sources}


def request_refresh() -> dict:
    if read_status().get("refreshing"):
        return {"ok": True, "queued": False, "alreadyQueued": True, "refreshing": True}
    settings.REFRESH_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    already_queued = settings.REFRESH_REQUEST_PATH.exists()
    atomic_write(settings.REFRESH_REQUEST_PATH, utc_now() + "\n")
    return {"ok": True, "queued": True, "alreadyQueued": already_queued}
