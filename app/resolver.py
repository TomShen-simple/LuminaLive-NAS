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
    throughput_mbps: float = 0.0


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


def channel_url_affinity(channel_id: str, url: str) -> int:
    """Prefer sources whose URL independently identifies the requested channel.

    Public IPTV lists occasionally label an opaque numeric relay incorrectly.
    A URL such as ``/live/cctv10hd.m3u8`` is stronger identity evidence than
    ``/tsfile/live/0011_1.m3u8`` even when the latter responds a little faster.
    """
    channel = channel_id.upper()
    if not channel.startswith("CCTV"):
        return 0
    searchable = re.sub(r"[^a-z0-9+]", "", urlparse(url).path.casefold())
    if channel == "CCTV5PLUS":
        return 2 if any(token in searchable for token in ("cctv5plus", "cctv5+", "cctv5p", "cctv5pul")) else 0
    match = re.fullmatch(r"CCTV(\d{1,2})", channel)
    if not match:
        return 0
    number = match.group(1)
    return 2 if re.search(rf"cctv0*{re.escape(number)}(?!\d)", searchable) else 0


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


def _redirect_host_allowed(original_url: str, final_url: str) -> bool:
    if urlparse(final_url).hostname == urlparse(original_url).hostname:
        return True
    return host_allowed(final_url)


def fetch(url: str, limit: int = 1_000_000, range_header: str | None = None) -> tuple[bytes, str]:
    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers)
    with urlopen(request, timeout=settings.HTTP_TIMEOUT) as response:
        final_url = response.geturl()
        if not _redirect_host_allowed(url, final_url):
            raise RuntimeError("redirect target disallowed")
        body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError("response too large")
        return body, final_url


def fetch_sample(
    url: str,
    sample_bytes: int = settings.PROBE_SAMPLE_BYTES,
    range_header: str | None = None,
) -> tuple[bytes, str, int]:
    """Download up to ``sample_bytes`` and return ``(data, final_url, transfer_ms)``.

    Servers that ignore ``Range`` and return ``200 OK`` with a full segment are
    capped cleanly at ``sample_bytes`` instead of failing as ``response too large``.
    """
    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers)
    started = time.monotonic()
    with urlopen(request, timeout=settings.HTTP_TIMEOUT) as response:
        final_url = response.geturl()
        if not _redirect_host_allowed(url, final_url):
            raise RuntimeError("redirect target disallowed")
        chunks: list[bytes] = []
        total = 0
        while total < sample_bytes:
            chunk = response.read(min(16_384, sample_bytes - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        transfer_ms = max(1, round((time.monotonic() - started) * 1000))
        return b"".join(chunks), final_url, transfer_ms


def looks_like_video_payload(data: bytes) -> bool:
    if len(data) < 564:
        return False
    prefix = data[:128].lstrip().lower()
    if prefix.startswith((b"<html", b"<!doctype", b"<xml", b"{", b"[", b"#extm3u")):
        return False
    limit = min(188, len(data) - 376)
    for offset in range(limit):
        if data[offset] == 0x47 and data[offset + 188] == 0x47 and data[offset + 376] == 0x47:
            return True
    head = data[:4096]
    return any(box in head for box in (b"ftyp", b"moov", b"moof", b"styp", b"sidx"))


def decode_manifest(url: str) -> tuple[str, str]:
    body, final_url = fetch(url, 500_000)
    text = body.decode("utf-8-sig", errors="replace")
    if not text.startswith("#EXTM3U"):
        raise RuntimeError("not HLS")
    upper = text.upper()
    if "#EXT-X-KEY" in upper or "#EXT-X-SESSION-KEY" in upper:
        raise RuntimeError("encrypted HLS is excluded")
    return text, final_url


def rank_variants(manifest: str, base_url: str) -> list[tuple[int, str]]:
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    choices: list[tuple[int, str]] = []
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF") or lines[index + 1].startswith("#"):
            continue
        match = BANDWIDTH_RE.search(line)
        choices.append((int(match.group(1)) if match else 0, urljoin(base_url, lines[index + 1])))
    if not choices:
        return []
    fitting = sorted(
        (item for item in choices if not item[0] or item[0] <= settings.MAX_BANDWIDTH),
        key=lambda item: item[0],
        reverse=True,
    )
    overflow = sorted(
        (item for item in choices if item[0] > settings.MAX_BANDWIDTH),
        key=lambda item: item[0],
    )
    return fitting + overflow


def choose_variant(manifest: str, base_url: str) -> str | None:
    ranked = rank_variants(manifest, base_url)
    return ranked[0][1] if ranked else None


def _probe_segment(segment_url: str) -> tuple[str, float]:
    if not host_allowed(segment_url):
        raise RuntimeError("segment host disallowed")
    sample_bytes = settings.PROBE_SAMPLE_BYTES
    segment, final_url, transfer_ms = fetch_sample(
        segment_url,
        sample_bytes=sample_bytes,
        range_header=f"bytes=0-{sample_bytes - 1}",
    )
    if len(segment) < 16_384 or not looks_like_video_payload(segment):
        raise RuntimeError("segment is not playable video")
    throughput_mbps = round((len(segment) * 8.0) / (transfer_ms * 1000.0), 2)
    if throughput_mbps < settings.MIN_STREAM_MBPS:
        raise RuntimeError("segment throughput too low")
    return final_url, throughput_mbps


def validate(url: str) -> Validated | None:
    if not host_allowed(url):
        return None
    started = time.monotonic()
    try:
        if urlparse(url).path.lower().endswith(".ts"):
            final_url, throughput_mbps = _probe_segment(url)
            return Validated(
                url=url,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                media_url=final_url,
                segment_url=final_url,
                throughput_mbps=throughput_mbps,
            )
        manifest, final_url = decode_manifest(url)
        variants = rank_variants(manifest, final_url)
        targets = variants[:2] if variants else [(0, final_url)]
        for idx, (variant_bw, target_url) in enumerate(targets):
            try:
                if variants:
                    sub_manifest, media_url = decode_manifest(target_url)
                else:
                    sub_manifest, media_url = manifest, target_url
                if "#EXT-X-ENDLIST" in sub_manifest or "#EXTINF:" not in sub_manifest:
                    continue
                uris = [
                    line.strip() for line in sub_manifest.splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                if not uris:
                    continue
                chosen_uri = uris[-2] if len(uris) >= 2 else uris[-1]
                segment_url = urljoin(media_url, chosen_uri)
                probed_segment_url, throughput_mbps = _probe_segment(segment_url)
                has_fallback = idx + 1 < len(targets)
                if has_fallback and variant_bw > 0 and throughput_mbps < (variant_bw / 1_000_000.0) * 1.1:
                    continue
                return Validated(
                    url=url,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    media_url=media_url,
                    segment_url=probed_segment_url,
                    throughput_mbps=throughput_mbps,
                )
            except (OSError, RuntimeError, UnicodeError, ValueError):
                continue
        return None
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
        # Keep local.m3u candidates ahead of remote lists, and within each tier
        # keep semantically identifiable URLs ahead of opaque relay numbers.
        unique.sort(key=lambda item: (item.source_order >= 0, -channel_url_affinity(channel_id, item.url), item.source_order))
        result[channel_id] = unique[: settings.MAX_CANDIDATES]
    return result


def _candidate_rank(candidate: Candidate, checked: Validated, affinity: int) -> tuple[int, int, float, int]:
    is_local = 1 if candidate.source_order < 0 else 0
    healthy_affinity = affinity if (
        checked.elapsed_ms <= 2_500
        and (checked.throughput_mbps <= 0 or checked.throughput_mbps >= settings.MIN_STREAM_MBPS)
    ) else 0
    capped_mbps = min(checked.throughput_mbps, 20.0)
    return (is_local, healthy_affinity, capped_mbps, -checked.elapsed_ms)


def resolve_channel(channel_id: str, candidates: list[Candidate]) -> tuple[str, Validated | None]:
    ordered = sorted(candidates, key=lambda item: item.source_order >= 0)
    best: Validated | None = None
    best_rank: tuple[int, int, float, int] | None = None
    best_is_local = False
    for candidate in ordered:
        if best is not None and best_is_local and candidate.source_order >= 0:
            break
        checked = validate(candidate.url)
        if not checked:
            continue
        affinity = channel_url_affinity(channel_id, candidate.url)
        rank = _candidate_rank(candidate, checked, affinity)
        if best_rank is None or rank > best_rank:
            best = checked
            best_rank = rank
            best_is_local = candidate.source_order < 0
            if candidate.source_order >= 0 and affinity > 0 and checked.elapsed_ms <= 800 and (
                checked.throughput_mbps <= 0 or checked.throughput_mbps >= 8.0
            ):
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
                "throughputMbps": item.throughput_mbps,
            }
            for channel_id, item in sorted(selected.items())
        },
    }
    atomic_write(settings.STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return status
