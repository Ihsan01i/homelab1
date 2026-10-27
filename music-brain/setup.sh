#!/usr/bin/env bash
set -euo pipefail

echo "======================================================"
echo "    Music Brain Vault Infrastructure - Auto Setup     "
echo "======================================================"

if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/opt/music-brain"

echo "[1/7] Installing system packages..."
apt-get update -y
apt-get install -y --no-install-recommends \
    curl wget ca-certificates git ffmpeg libchromaprint-tools \
    python3 python3-pip python3-venv samba sqlite3 jq rclone

echo "[2/7] Creating vault directories..."
mkdir -p "$TARGET_DIR/music" \
         "$TARGET_DIR/slskd/downloads" \
         "$TARGET_DIR/slskd/incomplete" \
         "$TARGET_DIR/slskd/data" \
         /var/lib/navidrome \
         /etc/navidrome \
         /etc/slskd

# If executed from outside /opt/music-brain, copy repository files into TARGET_DIR
if [ "$SCRIPT_DIR" != "$TARGET_DIR" ]; then
    echo "[*] Syncing application files to $TARGET_DIR..."
    mkdir -p "$TARGET_DIR/backend" "$TARGET_DIR/config" "$TARGET_DIR/systemd"
    cp -r "$SCRIPT_DIR/backend/"* "$TARGET_DIR/backend/"
    cp -r "$SCRIPT_DIR/config/"* "$TARGET_DIR/config/"
    cp -r "$SCRIPT_DIR/systemd/"* "$TARGET_DIR/systemd/"
    cp "$SCRIPT_DIR/requirements.txt" "$TARGET_DIR/"
    cp "$SCRIPT_DIR/setup.sh" "$TARGET_DIR/"
fi

echo "[3/7] Setting up Python virtual environment..."
if [ ! -d "$TARGET_DIR/.venv" ]; then
    python3 -m venv "$TARGET_DIR/.venv"
fi
"$TARGET_DIR/.venv/bin/pip" install --upgrade pip
"$TARGET_DIR/.venv/bin/pip" install -r "$TARGET_DIR/requirements.txt"

echo "[4/7] Installing Navidrome binary (if missing)..."
if [ ! -f /opt/navidrome/navidrome ]; then
    mkdir -p /opt/navidrome
    NAVI_VER="0.54.5"
    echo "Downloading Navidrome v${NAVI_VER}..."
    wget -qO /tmp/navidrome.tar.gz "https://github.com/navidrome/navidrome/releases/download/v${NAVI_VER}/navidrome_${NAVI_VER}_linux_amd64.tar.gz"
    tar -xzf /tmp/navidrome.tar.gz -C /opt/navidrome/
    chmod +x /opt/navidrome/navidrome
    rm /tmp/navidrome.tar.gz
fi

echo "[5/7] Installing slskd binary (if missing)..."
if [ ! -f /opt/slskd/slskd ]; then
    mkdir -p /opt/slskd
    SLSKD_VER="0.21.1"
    echo "Downloading slskd v${SLSKD_VER}..."
    wget -qO /tmp/slskd.zip "https://github.com/slskd/slskd/releases/download/${SLSKD_VER}/slskd-${SLSKD_VER}-linux-x64.zip"
    apt-get install -y unzip
    unzip -qo /tmp/slskd.zip -d /opt/slskd/
    chmod +x /opt/slskd/slskd
    rm /tmp/slskd.zip
fi

echo "[6/7] Installing configurations & systemd services..."
if [ -f "$TARGET_DIR/config/navidrome.toml" ]; then
    cp "$TARGET_DIR/config/navidrome.toml" /etc/navidrome/navidrome.toml
fi

if [ ! -f /etc/slskd/slskd.yml ] && [ -f "$TARGET_DIR/config/slskd.yml.example" ]; then
    cp "$TARGET_DIR/config/slskd.yml.example" /etc/slskd/slskd.yml
    echo "[!] Please edit /etc/slskd/slskd.yml with your Soulseek credentials!"
fi

cp "$TARGET_DIR"/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
cp "$TARGET_DIR"/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload

echo "[7/7] Enabling core services..."
systemctl enable navidrome slskd music-brain music-history-sync.timer webdav webdav-http
systemctl restart navidrome slskd music-brain music-history-sync.timer webdav webdav-http

echo "======================================================"
echo "    Music Brain Vault Infrastructure Setup Complete   "
echo "======================================================"
echo "Navidrome:   http://<ip>:4533"
echo "WebDAV HTTP: http://<ip>:8080"
echo "WebDAV SSL:  https://<ip>:8443"
echo "slskd:       http://<ip>:5030"
echo "Dashboard:   http://<ip>:8000"
echo "======================================================"
