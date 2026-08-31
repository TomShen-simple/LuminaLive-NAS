from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from . import settings


ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
BANDWIDTH_RE = re.compile(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", re.I)
RESOLUTION_TAG_RE = re.compile(r"\s*(?:\[[^\]]+\]|\([^)]*\))")


@dataclass(frozen=True)
class Candidate:
    name: str
    url: str
    source_order: int


@dataclass(frozen=True)
class Validated:
    url: str
    elapsed_ms: int
    media_url: str
    segment_url: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_name(value: str) -> str:
    name = RESOLUTION_TAG_RE.sub("", value).strip()
    aliases = {
        "Anhui TV": "安徽卫视",
        "Guangdong Satellite TV": "广东卫视",
        "Hebei TV": "河北卫视",
        "Hunan TV": "湖南卫视",
        "Shenzhen Satellite TV": "深圳卫视",
        "Zhejiang TV International": "浙江卫视",
    }
    if "BRTV 北京卫视" in name:
        return "北京卫视"
    for prefix, result in aliases.items():
        if name.startswith(prefix):
            return result
    cctv = re.search(r"CCTV[- ]?(\d{1,2})(\+)?", name, re.I)
    if cctv:
        return f"CCTV-{cctv.group(1)}{'+' if cctv.group(2) else ''}"
    return name


def parse_m3u(text: str, source_order: int = 0) -> list[Candidate]:
    result: list[Candidate] = []
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
        name = canonical_name(attrs.get("tvg-name") or label)
        if name:
            result.append(Candidate(name=name, url=line, source_order=source_order))
        pending = ""
    return result


def load_config(path: Path = settings.CHANNELS_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data.get("groups"), list) or not isinstance(data.get("source_playlists"), list):
        raise ValueError("channels.json must contain source_playlists and groups")
    return data


def host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if settings.ALLOW_PRIVATE:
        return True
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def fetch(url: str, limit: int = 1_000_000, range_header: str | None = None) -> tuple[bytes, str]:
    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers)
    with urlopen(request, timeout=settings.HTTP_TIMEOUT) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError("response too large")
        return body, response.geturl()


def decode_manifest(url: str) -> tuple[str, str]:
    body, final_url = fetch(url, 500_000)
    text = body.decode("utf-8-sig", errors="replace")
    if not text.startswith("#EXTM3U"):
        raise RuntimeError("not HLS")
    upper = text.upper()
    if "#EXT-X-KEY" in upper or "#EXT-X-SESSION-KEY" in upper:
        raise RuntimeError("encrypted HLS is excluded")
    return text, final_url


def choose_variant(manifest: str, base_url: str) -> str | None:
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    choices: list[tuple[int, str]] = []
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF") or lines[index + 1].startswith("#"):
            continue
        match = BANDWIDTH_RE.search(line)
        choices.append((int(match.group(1)) if match else 0, urljoin(base_url, lines[index + 1])))
    if not choices:
        return None
    fitting = [item for item in choices if not item[0] or item[0] <= settings.MAX_BANDWIDTH]
    return max(fitting or choices, key=lambda item: item[0])[1]


def validate(url: str) -> Validated | None:
    if not host_allowed(url):
        return None
    started = time.monotonic()
    try:
        manifest, final_url = decode_manifest(url)
        variant = choose_variant(manifest, final_url)
        if variant:
            manifest, final_url = decode_manifest(variant)
        if "#EXT-X-ENDLIST" in manifest or "#EXTINF:" not in manifest:
            return None
        uris = [
            line.strip() for line in manifest.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if not uris:
            return None
        segment_url = urljoin(final_url, uris[-1])
        if not host_allowed(segment_url):
            return None
        segment, _ = fetch(segment_url, 65_536, "bytes=0-65535")
        if not segment:
            return None
        return Validated(
            url=url,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            media_url=final_url,
            segment_url=segment_url,
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return None


def requested_channels(config: dict) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    lookup: dict[str, dict] = {}
    ordered: list[tuple[str, str]] = []
    for group in config["groups"]:
        group_title = str(group.get("title", "其他")).strip() or "其他"
        for item in group.get("channels", []):
            name = str(item["name"]).strip()
            channel_id = str(item.get("id") or name).strip()
            aliases = {canonical_name(name).casefold()}
            aliases.update(canonical_name(str(value)).casefold() for value in item.get("aliases", []))
            record = {"id": channel_id, "name": name, "group": group_title, "aliases": aliases}
            ordered.append((group_title, channel_id))
            for alias in aliases:
                lookup[alias] = record
    return lookup, ordered


def collect_candidates(config: dict) -> dict[str, list[Candidate]]:
    lookup, _ = requested_channels(config)
    result: dict[str, list[Candidate]] = {}
    source_urls = [str(value) for value in config["source_playlists"]] + list(settings.EXTRA_M3U_URLS)
    for source_order, source_url in enumerate(source_urls):
        if not host_allowed(source_url):
            continue
        try:
            text = fetch(source_url, 2_000_000)[0].decode("utf-8-sig", errors="replace")
        except (OSError, RuntimeError):
            continue
        for candidate in parse_m3u(text, source_order):
            record = lookup.get(candidate.name.casefold())
            if record:
                result.setdefault(record["id"], []).append(candidate)
    if settings.LOCAL_M3U_PATH.exists():
        text = settings.LOCAL_M3U_PATH.read_text(encoding="utf-8-sig", errors="replace")
        for candidate in parse_m3u(text, -1):
            record = lookup.get(candidate.name.casefold())
            if record:
                result.setdefault(record["id"], []).insert(0, candidate)
    for channel_id, candidates in result.items():
        unique: list[Candidate] = []
        seen: set[str] = set()
        for candidate in sorted(candidates, key=lambda item: item.source_order):
            if candidate.url not in seen:
                seen.add(candidate.url)
                unique.append(candidate)
        result[channel_id] = unique[: settings.MAX_CANDIDATES]
    return result


def resolve_channel(channel_id: str, candidates: list[Candidate]) -> tuple[str, Validated | None]:
    best: Validated | None = None
    for candidate in candidates:
        checked = validate(candidate.url)
        if checked and (best is None or checked.elapsed_ms < best.elapsed_ms):
            best = checked
            if checked.elapsed_ms <= 800:
                break
    return channel_id, best


def atomic_write(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return False
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def backup_playlist() -> None:
    if not settings.PLAYLIST_PATH.exists():
        return
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(settings.PLAYLIST_PATH, settings.BACKUP_DIR / f"playlist-{stamp}.m3u")
    for old in sorted(settings.BACKUP_DIR.glob("playlist-*.m3u"), reverse=True)[10:]:
        old.unlink()


def render(config: dict, selected: dict[str, Validated]) -> str:
    _, ordered = requested_channels(config)
    records = {
        str(item.get("id") or item["name"]): (str(item["name"]), str(group.get("title", "其他")))
        for group in config["groups"] for item in group.get("channels", [])
    }
    lines = ["#EXTM3U", f"#PLAYLIST:{config.get('playlist_name', 'LuminaLive NAS')}"]
    for _, channel_id in ordered:
        checked = selected.get(channel_id)
        if not checked:
            continue
        name, group = records[channel_id]
        lines.extend([
            f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="{group}",{name}',
            checked.url,
        ])
    return "\n".join(lines) + "\n"


def refresh() -> dict:
    started = time.monotonic()
    config = load_config()
    pool = collect_candidates(config)
    selected: dict[str, Validated] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.CHANNEL_WORKERS) as executor:
        futures = [executor.submit(resolve_channel, channel_id, values) for channel_id, values in pool.items()]
        for future in concurrent.futures.as_completed(futures):
            channel_id, checked = future.result()
            if checked:
                selected[channel_id] = checked
    if not selected:
        raise RuntimeError("no playable channels found; previous playlist kept")
    rendered = render(config, selected)
    backup_playlist()
    changed = atomic_write(settings.PLAYLIST_PATH, rendered)
    status = {
        "ok": True,
        "updatedAt": utc_now(),
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "published": len(selected),
        "requested": sum(len(group.get("channels", [])) for group in config["groups"]),
        "changed": changed,
        "channels": {
            channel_id: {
                "upstreamHost": urlparse(item.url).hostname,
                "validationMs": item.elapsed_ms,
            }
            for channel_id, item in sorted(selected.items())
        },
    }
    atomic_write(settings.STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return status
