#!/usr/bin/env bash
set -u

# Load environment securely if available
ENV_FILE="/etc/homelab/heartbeat.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$ENV_FILE"
fi

HC_URL="${HC_URL:-}"
if [[ -z "$HC_URL" ]]; then
    logger -t homelab-heartbeat "[ERROR] HC_URL not configured. Set HC_URL in /etc/homelab/heartbeat.env"
    exit 1
fi

UPTIME=$(uptime -p 2>/dev/null)
MEM_FREE=$(free -h | awk '/^Mem:/ {print $3 "/" $2}')
DISK_ROOT=$(df -h / | awk 'NR==2 {print $3 "/" $2 " (" $5 ")"}')

CT100=$(pct status 100 2>/dev/null || echo "stopped")
CT101=$(pct status 101 2>/dev/null || echo "stopped")
CT102=$(pct status 102 2>/dev/null || echo "stopped")

if ping -c 1 -W 2 100.114.251.120 >/dev/null 2>&1; then
    PI_STATUS="ONLINE"
else
    PI_STATUS="OFFLINE"
fi

BODY=$(cat <<EOM
Node: $(hostname)
Uptime: ${UPTIME}
RAM: ${MEM_FREE} | Disk: ${DISK_ROOT}
CT 100 (Beeper): ${CT100}
CT 101 (AdGuard): ${CT101}
CT 102 (Music): ${CT102}
Pi (100.114.251.120): ${PI_STATUS}
EOM
)

curl -fsS -m 10 --retry 3 --data-raw "$BODY" "$HC_URL" >/dev/null 2>&1
