FROM python:3.12-slim-bookworm

ARG TARGETARCH
LABEL org.opencontainers.image.title="LuminaLive NAS"
LABEL org.opencontainers.image.description="Self-hosted live TV playlist validator for Docker-capable NAS devices"
LABEL org.opencontainers.image.source="https://github.com/TomShen-simple/LuminaLive-NAS"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg tini tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/lumina-live
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config-default
COPY docker-entrypoint.sh /usr/local/bin/lumina-entrypoint
RUN chmod 0755 /usr/local/bin/lumina-entrypoint \
    && mkdir -p /config /data/backups

EXPOSE 8780
VOLUME ["/config", "/data"]
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8780/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/lumina-entrypoint"]

