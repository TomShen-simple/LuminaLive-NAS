from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import socket
import time
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web


MIGU_API = "https://play.miguvideo.com/playurl/v1/play/playurl"
MIGU_REFERER = "https://m.miguvideo.com/"
MIGU_APP_ID = "26000346"
MIGU_SIGN_SUFFIX_KEY = "2cac4f2c6c3346a5b34e085725ef7e33migu"
USER_AGENT = os.environ.get(
    "UPSTREAM_USER_AGENT",
    "Mozilla/5.0 (Linux; Android 14; TV) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
)
URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
LOG = logging.getLogger("lumina.migu")


class MiguRelay:
    """Resolve and proxy official Migu HLS entirely from the NAS network."""

    def __init__(self) -> None:
        self.access_token = os.environ.get("MIGU_RELAY_TOKEN", "").strip()
        self.signing_secret = os.environ.get("MIGU_RELAY_SIGNING_SECRET", "").strip()
        if not self.signing_secret:
            self.signing_secret = self.access_token
        self.enabled = bool(self.access_token and len(self.signing_secret) >= 24)
        self.resolved_ttl = max(60, int(os.environ.get("MIGU_RESOLVED_TTL", "180")))
        self.asset_ttl = max(120, int(os.environ.get("MIGU_ASSET_TOKEN_TTL", "900")))
        suffixes = os.environ.get("MIGU_ALLOWED_HOST_SUFFIXES", "miguvideo.com")
        self.allowed_suffixes = tuple(
            item.strip().lower().lstrip(".") for item in suffixes.split(",") if item.strip()
        )
        self.http: ClientSession | None = None
        self.resolved: dict[str, tuple[str, float]] = {}
        self.last_success_at: float | None = None
        self.last_error: str = ""

    async def start(self, _: web.Application) -> None:
        timeout = ClientTimeout(total=None, connect=7, sock_read=30)
        self.http = ClientSession(
            timeout=timeout,
            # Many NAS installations advertise IPv6 without having a usable
            # route. Migu's API/media endpoints are reliably available on IPv4.
            connector=TCPConnector(
                family=socket.AF_INET,
                limit=256,
                limit_per_host=128,
                ttl_dns_cache=300,
            ),
            auto_decompress=False,
        )

    async def stop(self, _: web.Application) -> None:
        if self.http is not None:
            await self.http.close()

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "officialApi": MIGU_API,
            "cachedPrograms": len(self.resolved),
            "lastSuccessAt": self.last_success_at,
            "lastError": self.last_error,
        }

    async def status_response(self, request: web.Request) -> web.Response:
        self.require_access(request)
        return web.json_response(
            self.status(),
            headers={"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"},
        )

    def require_access(self, request: web.Request) -> None:
        if not self.enabled:
            raise web.HTTPNotFound(text="Migu relay is disabled")
        supplied = request.query.get("access_token", "")
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not hmac.compare_digest(supplied, self.access_token):
            raise web.HTTPUnauthorized(text="invalid relay token")

    def allowed_url(self, value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme in {"http", "https"}
            and bool(host)
            and any(host == suffix or host.endswith(f".{suffix}") for suffix in self.allowed_suffixes)
        )

    def encode_asset(self, url: str) -> str:
        payload = json.dumps(
            {"url": url, "exp": int(time.time()) + self.asset_ttl},
            separators=(",", ":"),
        ).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self.signing_secret.encode(), body.encode(), hashlib.sha256).digest()
        return f"{body}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"

    @staticmethod
    def add_android_dd_calcu(url: str, program_id: str) -> str:
        """Finish Migu's Android media-URL handshake.

        The playurl API returns a URL containing ``puData``.  That value is
        intentionally incomplete: the Android client derives ``ddCalcu`` from
        it before asking the CDN for the manifest.  Sending the raw API URL is
        rejected by the CDN with its non-standard HTTP 661 response.
        """
        pu_data = parse_qs(urlparse(url).query, keep_blank_values=True).get("puData", [""])[-1]
        if not pu_data or len(program_id) < 7 or not program_id[6].isdigit():
            raise web.HTTPBadGateway(text="Migu API returned incomplete media credentials")

        keys = "cdabyzwxkl"
        date_key = keys[int(str(time.localtime().tm_year)[2])]
        program_key = keys[int(program_id[6])]
        dd_calcu: list[str] = []
        for index in range((len(pu_data) + 1) // 2):
            dd_calcu.extend((pu_data[-index - 1], pu_data[index]))
            if index == 1:
                dd_calcu.append("v")
            elif index == 2:
                dd_calcu.append(date_key)
            elif index == 3:
                dd_calcu.append(program_key)
            elif index == 4:
                dd_calcu.append("a")
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}ddCalcu={''.join(dd_calcu)}&sv=10004&ct=android"

    def decode_asset(self, token: str) -> str:
        try:
            body, supplied = token.split(".", 1)
            expected = hmac.new(self.signing_secret.encode(), body.encode(), hashlib.sha256).digest()
            signature = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid signature")
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            if int(payload.get("exp", 0)) < int(time.time()):
                raise ValueError("expired token")
            url = str(payload.get("url", ""))
            if not self.allowed_url(url):
                raise ValueError("invalid upstream")
            return url
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise web.HTTPForbidden(text="invalid asset token") from exc

    def rewrite_manifest(self, manifest: str, base_url: str) -> str:
        def proxy_url(value: str) -> str:
            absolute = urljoin(base_url, value)
            if not self.allowed_url(absolute):
                raise web.HTTPBadGateway(text="Migu manifest contains an untrusted host")
            return f"/api/migu/asset/{self.encode_asset(absolute)}"

        output: list[str] = []
        for raw in manifest.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                line = URI_ATTRIBUTE_RE.sub(lambda match: f'URI="{proxy_url(match.group(1))}"', line)
            elif line:
                line = proxy_url(line)
            output.append(line)
        return "\n".join(output) + "\n"

    async def resolve(self, program_id: str) -> str:
        if not re.fullmatch(r"[0-9]{4,32}", program_id):
            raise web.HTTPBadRequest(text="invalid Migu program id")
        cached = self.resolved.get(program_id)
        if cached and time.monotonic() - cached[1] < self.resolved_ttl:
            return cached[0]
        assert self.http is not None
        timestamp = str(int(time.time() * 1000))
        salt = f"{secrets.randbelow(1_000_000):06d}25"
        inner = hashlib.md5(f"{timestamp}{program_id}{MIGU_APP_ID}".encode()).hexdigest()
        sign = hashlib.md5(f"{inner}{MIGU_SIGN_SUFFIX_KEY}{salt[:4]}".encode()).hexdigest()
        query = urlencode({
            "sign": sign,
            "rateType": "3",
            "contId": program_id,
            "timestamp": timestamp,
            "salt": salt,
            "flvEnable": "true",
            "super4k": "true",
        })
        headers = {
            "User-Agent": USER_AGENT,
            "AppVersion": "2600034600",
            "TerminalId": "android",
            "X-UP-CLIENT-CHANNEL-ID": "2600034600-99000-201600010010028",
            "Referer": MIGU_REFERER,
            "Accept": "application/json,*/*",
            "Accept-Encoding": "identity",
        }
        try:
            async with self.http.get(f"{MIGU_API}?{query}", headers=headers) as response:
                if response.status != 200:
                    raise web.HTTPBadGateway(text=f"Migu API HTTP {response.status}")
                payload = await response.json(content_type=None)
            if str(payload.get("code")) != "200":
                reason = payload.get("rid") or payload.get("code") or "unknown"
                raise web.HTTPBadGateway(text=f"Migu API {reason}")
            body = payload.get("body") or {}
            url = str((body.get("urlInfo") or {}).get("url") or "")
            if not self.allowed_url(url):
                raise web.HTTPBadGateway(text="Migu API returned an invalid media URL")
            resolved_program_id = str((body.get("content") or {}).get("contId") or program_id)
            url = self.add_android_dd_calcu(url, resolved_program_id)
            self.resolved[program_id] = (url, time.monotonic())
            self.last_success_at = time.time()
            self.last_error = ""
            return url
        except web.HTTPException as exc:
            self.last_error = exc.text
            raise
        except Exception as exc:
            detail = str(exc).strip().replace("\n", " ")[:240]
            self.last_error = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            LOG.warning("Migu API request failed for program %s: %s", program_id, self.last_error)
            raise web.HTTPBadGateway(text="Migu API request failed") from exc

    @staticmethod
    def upstream_headers() -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Referer": MIGU_REFERER, "Accept": "*/*"}

    @classmethod
    def _read_manifest_compat(cls, url: str) -> tuple[bytes, str, int]:
        # Migu's gslb endpoint emits a legal-enough 302 that curl and Android
        # accept, but aiohttp's strict parser rejects the trailing HTML as a
        # second malformed response.  urllib mirrors the tolerant client
        # behaviour and is used only for these tiny HLS manifests.
        request = urllib_request.Request(url, headers=cls.upstream_headers())
        with urllib_request.urlopen(request, timeout=30) as response:
            return response.read(1_000_001), response.geturl(), int(response.status)

    async def fetch_manifest(self, url: str) -> tuple[str, str]:
        try:
            data, final_url, status = await asyncio.to_thread(self._read_manifest_compat, url)
        except Exception as exc:
            detail = str(exc).strip().replace("\n", " ")[:180]
            self.last_error = f"manifest {type(exc).__name__}: {detail}"
            raise web.HTTPBadGateway(text="Migu manifest request failed") from exc
        if status != 200:
            raise web.HTTPBadGateway(text=f"Migu manifest HTTP {status}")
        if not self.allowed_url(final_url):
            raise web.HTTPBadGateway(text="Migu redirected to an untrusted host")
        if len(data) > 1_000_000:
            raise web.HTTPBadGateway(text="Migu manifest is too large")
        text = data.decode("utf-8-sig", errors="replace")
        if not text.startswith("#EXTM3U"):
            raise web.HTTPBadGateway(text="Migu response is not HLS")
        return text, final_url

    async def index(self, request: web.Request) -> web.Response:
        self.require_access(request)
        url = await self.resolve(request.match_info["program_id"])
        manifest, final_url = await self.fetch_manifest(url)
        return web.Response(
            text=self.rewrite_manifest(manifest, final_url),
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "no-store",
                "X-Lumina-Upstream": "migu-official",
                "X-Lumina-Program-ID": request.match_info["program_id"],
            },
        )

    async def asset(self, request: web.Request) -> web.StreamResponse:
        url = self.decode_asset(request.match_info["token"])
        assert self.http is not None
        headers = self.upstream_headers()
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]
        async with self.http.request(request.method, url, headers=headers, allow_redirects=True) as upstream:
            if upstream.status >= 400:
                raise web.HTTPBadGateway(text=f"Migu asset HTTP {upstream.status}")
            final_url = str(upstream.url)
            if not self.allowed_url(final_url):
                raise web.HTTPBadGateway(text="Migu asset redirected to an untrusted host")
            content_type = upstream.headers.get("Content-Type", "").lower()
            is_manifest = "mpegurl" in content_type or urlparse(final_url).path.lower().endswith(".m3u8")
            if is_manifest:
                data = await upstream.content.read(1_000_001)
                if len(data) > 1_000_000:
                    raise web.HTTPBadGateway(text="Migu manifest is too large")
                text = data.decode("utf-8-sig", errors="replace")
                if not text.startswith("#EXTM3U"):
                    raise web.HTTPBadGateway(text="Migu response is not HLS")
                return web.Response(
                    text=self.rewrite_manifest(text, final_url),
                    content_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"},
                )
            response_headers = {
                key: value for key, value in upstream.headers.items()
                if key.lower() in {"content-type", "content-length", "content-range", "accept-ranges"}
            }
            response_headers["Cache-Control"] = "public, max-age=60, immutable"
            downstream = web.StreamResponse(status=upstream.status, headers=response_headers)
            await downstream.prepare(request)
            if request.method != "HEAD":
                async for chunk in upstream.content.iter_chunked(128 * 1024):
                    await downstream.write(chunk)
            await downstream.write_eof()
            return downstream
