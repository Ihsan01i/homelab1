#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root on the Proxmox VE host."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/4] Creating secure configuration directory..."
mkdir -p /etc/homelab
chmod 700 /etc/homelab

echo "[2/4] Setting up heartbeat.env..."
if [ ! -f /etc/homelab/heartbeat.env ]; then
    if [ -f "$SCRIPT_DIR/heartbeat.env.example" ]; then
        cp "$SCRIPT_DIR/heartbeat.env.example" /etc/homelab/heartbeat.env
        chmod 600 /etc/homelab/heartbeat.env
        echo "[!] Created /etc/homelab/heartbeat.env. Please edit with your private Healthchecks.io URL!"
    fi
else
    chmod 600 /etc/homelab/heartbeat.env
    echo "[*] Existing /etc/homelab/heartbeat.env preserved."
fi

echo "[3/4] Installing heartbeat script to /usr/local/bin..."
cp "$SCRIPT_DIR/homelab-heartbeat.sh" /usr/local/bin/homelab-heartbeat.sh
chmod 755 /usr/local/bin/homelab-heartbeat.sh

echo "[4/4] Installing cron job to /etc/cron.d/..."
cp "$SCRIPT_DIR/homelab-heartbeat.cron" /etc/cron.d/homelab-heartbeat
chmod 644 /etc/cron.d/homelab-heartbeat

echo "=== Heartbeat installation complete ==="
echo "Run manually to test: /usr/local/bin/homelab-heartbeat.sh"
