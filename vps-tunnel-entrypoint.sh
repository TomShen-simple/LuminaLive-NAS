#!/bin/sh
set -eu

: "${VPS_HOST:?VPS_HOST is required}"
: "${VPS_SSH_USER:=opc}"
: "${VPS_RELAY_PORT:=18782}"

case "$VPS_RELAY_PORT" in
  *[!0-9]*|'') echo "VPS_RELAY_PORT must be numeric" >&2; exit 2 ;;
esac

# 极空间 Compose 编辑器不便额外上传密钥文件，因此支持从 .env 注入
# base64；专用公钥在 VPS 上已限制为仅能监听一个回环端口。
if [ -n "${VPS_TUNNEL_KEY_B64:-}" ]; then
  printf '%s' "$VPS_TUNNEL_KEY_B64" | base64 -d > /tmp/vps_tunnel_key
elif [ -f /run/secrets/vps_tunnel_key ]; then
  cp /run/secrets/vps_tunnel_key /tmp/vps_tunnel_key
else
  echo "VPS tunnel key is missing" >&2
  exit 2
fi
chmod 600 /tmp/vps_tunnel_key

if [ -n "${VPS_KNOWN_HOSTS_B64:-}" ]; then
  printf '%s' "$VPS_KNOWN_HOSTS_B64" | base64 -d > /tmp/known_hosts
elif [ -f /run/secrets/known_hosts ]; then
  cp /run/secrets/known_hosts /tmp/known_hosts
else
  echo "VPS known_hosts is missing" >&2
  exit 2
fi
chmod 600 /tmp/known_hosts

exec /usr/bin/autossh \
  -M 0 -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/tmp/known_hosts \
  -i /tmp/vps_tunnel_key \
  -R "127.0.0.1:${VPS_RELAY_PORT}:lumina-live:8780" \
  "${VPS_SSH_USER}@${VPS_HOST}"
