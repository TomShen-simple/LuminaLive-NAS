"""One-shot read-only NAS/VPS video comparison; outputs no signed URLs."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def env(path: str) -> dict[str, str]:
    result = {}
    for line in Path(path).read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and not key.startswith('#'):
            result[key] = value.strip("\"'")
    return result


secrets = {**env('/etc/lumina-resolver.env'), **env('/etc/lumina-official-gateway.env')}
nas = 'http://127.0.0.1:18786'
gate = 'http://127.0.0.1:8783/live-official/' + secrets['OFFICIAL_PLAYBACK_KEY']
channels = {'CCTV1': '608807420', 'CCTV5': '641886683',
            'CCTV5+': '641886773', 'CCTV10': '624878405',
            'CCTV13': '608807423', '东方卫视': '651632648'}


def read(url: str, source: str, limit: int) -> bytes:
    headers = {'User-Agent': 'APTV'}
    if source == 'nas':
        headers['Authorization'] = 'Bearer ' + secrets['CCTV_MIGU_RELAY_TOKEN']
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('response too large')
    return data


def probe(item: tuple[str, str], source: str) -> dict:
    name, pid = item
    base = nas if source == 'nas' else gate
    started = time.monotonic()
    result = {'channel': name, 'source': source}
    try:
        index = base + '/api/migu/' + pid + '/index.m3u8'
        playlist = read(index, source, 1_000_000).decode('utf-8-sig')
        entries = [urljoin(index, line) for line in playlist.splitlines()
                   if line and not line.startswith('#')]
        seq = re.search(r'(?m)^#EXT-X-MEDIA-SEQUENCE:(\d+)', playlist)
        result.update(sequence=int(seq[1]) if seq else None,
                      segments=len(entries), manifestSeconds=round(time.monotonic()-started, 2),
                      adMarkers=any(tag in playlist.upper() for tag in ('CUE-OUT', 'SCTE35', 'INTERSTITIAL')))
        if not entries:
            raise ValueError('no segment')
        media = read(entries[-1], source, 8_000_000)
        result.update(segmentBytes=len(media), segmentSha256=hashlib.sha256(media).hexdigest(),
                      prefixSha256=hashlib.sha256(media[:65536]).hexdigest(),
                      video=bool(len(media)>376 and media[0] == media[188] == 0x47),
                      elapsedSeconds=round(time.monotonic()-started, 2))
        if name == 'CCTV5' and source == 'gate':
            # Keep one bounded diagnostic sample for offline video-frame QA.
            destination = Path('/tmp/lumina-cctv5-current.ts')
            destination.write_bytes(media)
            result['samplePath'] = str(destination)
    except Exception as exc:
        result.update(error=type(exc).__name__, status=getattr(exc, 'code', None),
                      elapsedSeconds=round(time.monotonic()-started, 2))
    return result


if __name__ == '__main__':
    print(json.dumps({'capturedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}), flush=True)
    jobs = [(item, source) for item in channels.items() for source in ('nas', 'gate')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for result in executor.map(lambda job: probe(*job), jobs):
            print(json.dumps(result, ensure_ascii=False), flush=True)
