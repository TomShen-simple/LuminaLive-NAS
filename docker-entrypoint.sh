#!/bin/sh
set -eu

mkdir -p /config /data/backups
if [ ! -f /config/channels.json ]; then
  cp /opt/lumina-live/config-default/channels.json /config/channels.json
fi

python -m app.scheduler &
scheduler_pid=$!

terminate() {
  kill "$scheduler_pid" 2>/dev/null || true
  wait "$scheduler_pid" 2>/dev/null || true
}
trap terminate INT TERM EXIT

python -m app.server

