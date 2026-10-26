#!/bin/bash
set -u

MIN_BATTERY=40
ETH_IFACE="nic0"

BAT_CAP=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -n1)
BAT_STATUS=$(cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -n1)
AC_ONLINE=$(cat /sys/class/power_supply/AC*/online 2>/dev/null || cat /sys/class/power_supply/ADP*/online 2>/dev/null | head -n1)

BAT_CAP=${BAT_CAP:-100}
BAT_STATUS=${BAT_STATUS:-"Unknown"}
AC_ONLINE=${AC_ONLINE:-1}

if [[ "$AC_ONLINE" -eq 0 || "$BAT_STATUS" == "Discharging" ]]; then
    if (( BAT_CAP <= MIN_BATTERY )); then
        logger -t battery-guard "Power outage detected! Battery at ${BAT_CAP}%. Arming WOL and suspending..."
        ethtool -s "$ETH_IFACE" wol g
        systemctl suspend
    fi
fi
