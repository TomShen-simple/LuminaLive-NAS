#!/bin/sh
set -eu

: "${VPS_HOST:?VPS_HOST is required}"
: "${VPS_SSH_USER:=opc}"
: "${VPS_RELAY_PORT:=18782}"

case "$VPS_RELAY_PORT" in
  *[!0-9]*|'') echo "VPS_RELAY_PORT must be numeric" >&2; exit 2 ;;
esac

# Bind-mounted private keys are often 0444 on NAS Compose implementations.
# Copy into tmpfs with OpenSSH-safe permissions without altering the host file.
install -m 600 /run/secrets/vps_tunnel_key /tmp/vps_tunnel_key

exec /usr/bin/autossh \
  -M 0 -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/secrets/known_hosts \
  -i /tmp/vps_tunnel_key \
  -R "127.0.0.1:${VPS_RELAY_PORT}:lumina-live:8780" \
  "${VPS_SSH_USER}@${VPS_HOST}"
