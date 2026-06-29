#!/usr/bin/env bash
# sandbox-iptables.sh — Prod egress allowlist for the sandbox_net Docker bridge.
#
# Run on the Hyper-V Linux VM AFTER Docker has started and
# `docker compose -f deploy/docker-compose.prod.yml up -d` has completed
# (so sandbox_net exists).
#
# What these rules do:
#   - Allow traffic from sandbox containers to the MCP fileserver(s).
#   - Block traffic to Qdrant, the app DB, the Windows host, and all other
#     RFC-1918 private ranges (the "network wall" from §sandbox/README.md).
#   - Public internet egress is unblocked (needed for download_url in the runner).
#
# Usage:
#   FILESERVER_IP=10.0.0.5 FILESERVER_PORT=8080 \
#   QDRANT_IP=127.0.0.1 APP_HOST_IP=192.168.1.10 \
#   bash deploy/sandbox-iptables.sh
#
# All variables default to localhost / loopback so the script is safe to run
# without arguments in a test environment, but you MUST set real values in prod.
#
# To persist rules across reboots (Ubuntu/Debian):
#   apt install iptables-persistent
#   iptables-save > /etc/iptables/rules.v4
# RHEL/CentOS: service iptables save

set -euo pipefail

FILESERVER_IP="${FILESERVER_IP:-127.0.0.1}"
FILESERVER_PORT="${FILESERVER_PORT:-8080}"
QDRANT_IP="${QDRANT_IP:-127.0.0.1}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
APP_HOST_IP="${APP_HOST_IP:-}"    # Windows host IP; blank = skip rule
APP_DB_IP="${APP_DB_IP:-}"        # only needed if using remote Postgres; blank = skip

# Identify the sandbox_net bridge interface.
SANDBOX_BR=$(docker network inspect sandbox_net --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || true)

if [[ -z "$SANDBOX_BR" ]]; then
    # Fallback: grep ip link for br- interfaces and pick the one for sandbox_net.
    SANDBOX_NET_ID=$(docker network inspect sandbox_net --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    SANDBOX_BR="br-${SANDBOX_NET_ID}"
fi

if [[ -z "$SANDBOX_BR" ]] || ! ip link show "$SANDBOX_BR" &>/dev/null; then
    echo "ERROR: Could not determine sandbox_net bridge interface." >&2
    echo "Ensure 'docker compose -f deploy/docker-compose.prod.yml up -d' ran first," >&2
    echo "then check: docker network inspect sandbox_net" >&2
    exit 1
fi

echo "Applying iptables rules for sandbox bridge: $SANDBOX_BR"

# ---- Allow MCP fileserver(s) FIRST (before the DROP rules) ------------------
iptables -I FORWARD -i "$SANDBOX_BR" \
    -d "$FILESERVER_IP" -p tcp --dport "$FILESERVER_PORT" -j ACCEPT
echo "  ACCEPT → fileserver $FILESERVER_IP:$FILESERVER_PORT"

# ---- Block traffic to internal services -------------------------------------

# Qdrant
iptables -I FORWARD -i "$SANDBOX_BR" \
    -d "$QDRANT_IP" -p tcp --dport "$QDRANT_PORT" -j DROP
echo "  DROP   → Qdrant $QDRANT_IP:$QDRANT_PORT"

# App DB (Postgres — only if APP_DB_IP is set)
if [[ -n "$APP_DB_IP" ]]; then
    APP_DB_PORT="${APP_DB_PORT:-5432}"
    iptables -I FORWARD -i "$SANDBOX_BR" \
        -d "$APP_DB_IP" -p tcp --dport "$APP_DB_PORT" -j DROP
    echo "  DROP   → app DB $APP_DB_IP:$APP_DB_PORT"
fi

# Windows host (backend) — if APP_HOST_IP is set
if [[ -n "$APP_HOST_IP" ]]; then
    iptables -I FORWARD -i "$SANDBOX_BR" -d "$APP_HOST_IP" -p tcp -j DROP
    echo "  DROP   → app host $APP_HOST_IP"
fi

# VM loopback — Docker containers can sometimes reach 127.0.0.1 via host-routing
iptables -I FORWARD -i "$SANDBOX_BR" -d 127.0.0.0/8 -j DROP
echo "  DROP   → loopback 127.0.0.0/8"

# All RFC-1918 private ranges (catches any remaining internal hosts)
iptables -I FORWARD -i "$SANDBOX_BR" -d 10.0.0.0/8 -j DROP
iptables -I FORWARD -i "$SANDBOX_BR" -d 172.16.0.0/12 -j DROP
iptables -I FORWARD -i "$SANDBOX_BR" -d 192.168.0.0/16 -j DROP
echo "  DROP   → RFC-1918 10/8, 172.16/12, 192.168/16"

# ---- Public internet --------------------------------------------------------
# Docker's default ACCEPT FORWARD policy + MASQUERADE rule already allow egress
# to public IPs.  The DROP rules above narrow that to block private ranges only.
echo "  ACCEPT → public internet (existing Docker MASQUERADE rule)"

echo ""
echo "Rules applied successfully."
echo ""
echo "To persist across reboots (Ubuntu/Debian):"
echo "  apt install iptables-persistent && iptables-save > /etc/iptables/rules.v4"
echo "RHEL/CentOS: service iptables save"
