#!/bin/sh
# Starts Tailscale (when TS_AUTHKEY is set) and then WatchFox.
set -e
if [ -n "$TS_AUTHKEY" ]; then
  mkdir -p /tmp/tailscale
  # Azure App Service containers only show a link-local address on eth0, so Tailscale decides there is no network and never signs in.
  # The platform does provide outbound internet, so tell it to assume the network is up.
  export TS_ASSUME_NETWORK_UP_FOR_TEST=1 TS_FORCE_NOISE_443=1
  tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055 \
             --state=/tmp/tailscale/tailscaled.state --socket=/tmp/tailscale/tailscaled.sock >/tmp/tailscaled.log 2>&1 &
  for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /tmp/tailscale/tailscaled.sock ] && break; sleep 1; done
  tailscale --socket=/tmp/tailscale/tailscaled.sock up --authkey="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-watchfox-app}" --accept-dns=false \
            --timeout=60s || {
    echo "[tailscale] could not join the tailnet: the agent server will not be reachable"
    tailscale --socket=/tmp/tailscale/tailscaled.sock status 2>&1 | head -8 | sed 's/^/[tailscale] /'
    grep -vE 'localapi: \[|RATELIMIT' /tmp/tailscaled.log | head -70 | sed 's/^/[tailscaled] /' | cut -c1-230
    python3 - <<'PY' 2>&1 | sed 's/^/[net] /'
import socket, ssl, urllib.request
for host in ('controlplane.tailscale.com', 'login.tailscale.com'):
    try:
        print(host, socket.gethostbyname(host))
        print(host, 'https', urllib.request.urlopen(f'https://{host}/', timeout=8).status)
    except Exception as e:
        print(host, 'ERR', type(e).__name__, str(e)[:120])
PY
  }
  # only the agent calls go through the tailnet; everything else uses the normal network
  export LAYA_PROXY="${LAYA_PROXY:-socks5://127.0.0.1:1055}"
fi
exec watchfox serve
