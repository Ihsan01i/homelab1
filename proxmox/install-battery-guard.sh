#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p /opt/homelab
cp "$SCRIPT_DIR/battery-guard.sh" /opt/homelab/battery-guard.sh
chmod +x /opt/homelab/battery-guard.sh

cp "$SCRIPT_DIR/battery-guard.service" /etc/systemd/system/battery-guard.service
cp "$SCRIPT_DIR/battery-guard.timer" /etc/systemd/system/battery-guard.timer

systemctl daemon-reload
systemctl enable --now battery-guard.timer
systemctl status battery-guard.timer --no-pager
