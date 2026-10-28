#!/bin/bash
PROFILE_DIR="/opt/music-brain/.browser_profile"
mkdir -p "$PROFILE_DIR"

# Cleanup previous instances
pkill -f "Xvfb :99" 2>/dev/null
pkill -f "x11vnc -display :99" 2>/dev/null
pkill -f "websockify.*6080" 2>/dev/null
pkill -f "chromium.*browser_profile" 2>/dev/null
sleep 1

echo "[1/4] Starting Virtual Display :99..."
Xvfb :99 -screen 0 1280x800x24 > /dev/null 2>&1 &
sleep 1

echo "[2/4] Starting VNC Server..."
x11vnc -display :99 -nopw -listen 127.0.0.1 -forever > /dev/null 2>&1 &
sleep 1

echo "[3/4] Starting NoVNC Web Gateway on port 6080..."
websockify --web=/usr/share/novnc 6080 localhost:5900 > /dev/null 2>&1 &
sleep 1

echo "[4/4] Launching Chromium with persistent profile..."
DISPLAY=:99 chromium \
    --no-sandbox \
    --user-data-dir="$PROFILE_DIR" \
    --window-size=1280,800 \
    --disable-gpu \
    --disable-dev-shm-usage \
    "https://music.youtube.com" > /dev/null 2>&1 &

echo ""
echo "=================================================================="
echo " NoVNC Login Screen is LIVE!"
echo " Open in your browser: http://192.168.1.102:6080/vnc.html"
echo " (Click 'Connect' if prompted, then sign into YouTube Music)"
echo "=================================================================="
