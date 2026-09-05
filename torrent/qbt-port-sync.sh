#!/bin/sh
# Push ProtonVPN's NAT-PMP forwarded port into qBittorrent's listen_port.
#
# gluetun runs this as VPN_PORT_FORWARDING_UP_COMMAND every time the lease
# changes — which is on every reconnect, and Proton hands out a *different*
# random port each time. Without this the client keeps listening on a port
# nothing is forwarded to: downloads still work, but you are unconnectable to
# peers who can't initiate, and seeding is close to dead. It looks like "the
# VPN is slow" rather than like a misconfiguration, which is why it's wired up
# automatically instead of left as a setting to remember.
#
# This runs *inside gluetun's network namespace*, which qBittorrent shares. So
# 127.0.0.1:8080 here genuinely is loopback and qBittorrent's "bypass
# authentication for clients on localhost" covers it — no credentials needed.
# Lunaschal's own calls arrive from the Docker bridge instead, are not
# localhost, and authenticate properly. That split is deliberate: the only
# things in this namespace are gluetun and qBittorrent.

set -u

PORT="${1:-}"
# gluetun substitutes {{PORTS}}, which is comma-separated in the general case.
# Proton only ever forwards one, so take the first and ignore any others.
PORT="${PORT%%,*}"

case "$PORT" in
  '' | *[!0-9]*)
    echo "qbt-port-sync: refusing to set a non-numeric port: '${1:-}'" >&2
    exit 1
    ;;
esac

QBT="http://127.0.0.1:8080"

# gluetun can win the race — the NAT-PMP lease often lands before qBittorrent's
# WebUI is listening, and a single attempt would then silently drop the port
# until the next reconnect (hours later).
i=0
while [ "$i" -lt 60 ]; do
  if wget -q -O /dev/null "$QBT/api/v2/app/version" 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge 60 ]; then
  echo "qbt-port-sync: qBittorrent WebUI never came up; port $PORT not applied" >&2
  exit 1
fi

if wget -q -O /dev/null \
  --post-data "json={\"listen_port\":$PORT,\"random_port\":false,\"upnp\":false}" \
  "$QBT/api/v2/app/setPreferences"; then
  echo "qbt-port-sync: listen_port set to $PORT"
else
  echo "qbt-port-sync: failed to set listen_port to $PORT" >&2
  exit 1
fi
