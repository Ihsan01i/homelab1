#!/bin/bash
set -euo pipefail

echo "Installing prerequisites (wakeonlan, dnsutils, curl)..."
sudo apt-get update -y
sudo apt-get install -y wakeonlan dnsutils curl

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$SCRIPT_DIR/watchdog.sh"

CRON_LINE="* * * * * $SCRIPT_DIR/watchdog.sh"
( crontab -l 2>/dev/null | grep -Fv "$SCRIPT_DIR/watchdog.sh" ; echo "$CRON_LINE" ) | crontab -

echo "Watchdog scheduled every minute in crontab."
