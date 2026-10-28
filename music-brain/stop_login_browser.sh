#!/bin/bash
echo "Stopping temporary NoVNC display and Chromium..."
pkill -f "chromium.*browser_profile" 2>/dev/null
pkill -f "websockify.*6080" 2>/dev/null
pkill -f "x11vnc -display :99" 2>/dev/null
pkill -f "Xvfb :99" 2>/dev/null
echo "Done! Session saved in /opt/music-brain/.browser_profile."
