#!/bin/bash
set -u

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$BASE/watchdog.log"
STATE="$BASE/state"
LOCKFILE="/tmp/homelab-watchdog.lock"
ENV_FILE="$BASE/watchdog.env"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$ENV_FILE"
fi

TOPIC="${NTFY_TOPIC:-}"
PROXMOX_MAC="${PROXMOX_MAC:-d4:5d:64:66:bc:66}"
PROXMOX_BROADCAST="${PROXMOX_BROADCAST:-192.168.1.255}"

mkdir -p "$BASE" "$STATE"

exec 200>"$LOCKFILE"
if ! flock -n 200; then
    exit 0
fi

sleep 10

send_alert() {
    local message="$1"
    if [[ -z "$TOPIC" ]]; then
        echo "$(date '+%F %T') [WARNING] NTFY_TOPIC not configured; alert skipped: $message" >> "$LOG"
        return
    fi
    curl -fsS --max-time 10 \
        -d "$message" \
        "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || \
        echo "$(date '+%F %T') [ALERT FAILED] $message" >> "$LOG"
}

check_service() {
    local name="$1"
    local key="$2"
    shift 2

    local state_file="$STATE/$key"
    local fail_file="$STATE/${key}_failures"

    local previous_state="UP"
    [[ -f "$state_file" ]] && previous_state="$(cat "$state_file")"

    local failures=0
    [[ -f "$fail_file" ]] && failures="$(cat "$fail_file")"

    if "$@" >/dev/null 2>&1; then
        echo 0 > "$fail_file"

        if [[ "$previous_state" == "DOWN" ]]; then
            echo "UP" > "$state_file"
            echo "$(date '+%F %T') [RECOVERED] $name is UP" >> "$LOG"
            send_alert "$name is UP on your homelab."
        fi
    else
        failures=$((failures + 1))
        echo "$failures" > "$fail_file"

        if [[ "$previous_state" == "UP" ]]; then
            if (( failures < 3 )); then
                echo "$(date '+%F %T') [WARNING $failures/3] $name check failed" >> "$LOG"
            else
                echo "DOWN" > "$state_file"
                echo "$(date '+%F %T') [OUTAGE] $name is DOWN" >> "$LOG"
                send_alert "$name is DOWN on your homelab."
            fi
        fi

        if [[ "$key" == "proxmox" && "$failures" -ge 3 ]]; then
            if command -v wakeonlan >/dev/null 2>&1; then
                wakeonlan -i "$PROXMOX_BROADCAST" "$PROXMOX_MAC" >> "$LOG" 2>&1
                echo "$(date '+%F %T') [WOL SENT] Dispatched magic packet to $PROXMOX_MAC" >> "$LOG"
            else
                echo "$(date '+%F %T') [ERROR] wakeonlan binary not found in PATH" >> "$LOG"
            fi
        fi
    fi
}

check_service "Proxmox HTTPS" proxmox \
    curl -k -s --max-time 5 https://192.168.1.100:8006/

check_service "AdGuard DNS" adguard \
    dig @192.168.1.210 example.com +time=2 +tries=1
