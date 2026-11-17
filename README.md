# Homelab1

Scripts and notes for my home server. Proxmox runs on an old laptop, a Raspberry Pi watches it, the laptop battery acts as a small UPS, and dedicated LXC containers run network services and a personal lossless music vault.

## Hardware

| Device | Role | IP |
|---|---|---|
| ASUS TUF FX505DT | Proxmox VE host | 192.168.1.100 |
| LXC container on Proxmox | AdGuard Home (DNS) | 192.168.1.210 |
| LXC container on Proxmox | Music Brain (Lossless Vault & Streaming) | 192.168.1.102 |
| Raspberry Pi 3B+ | Watchdog, Wake-on-LAN sender, Tailscale node | 192.168.1.201 |

## What's in the repo

```
watchdog/      runs on the Pi
  watchdog.sh
  install-watchdog.sh
  watchdog.env.example
proxmox/       runs on the Proxmox host
  battery-guard.sh
  battery-guard.service
  battery-guard.timer
  install-battery-guard.sh
  homelab-heartbeat.sh
  homelab-heartbeat.cron
  install-heartbeat.sh
  heartbeat.env.example
music-brain/   runs on CT 102 (Music Brain LXC)
  setup.sh
  requirements.txt
  backend/app/
  config/
  systemd/
```

## How it works

**watchdog.sh** (Pi, run from cron every minute)
- Waits 10 seconds, then checks two things:
  - Proxmox web UI: `curl -k https://192.168.1.100:8006/` (5 s timeout)
  - AdGuard: `dig @192.168.1.210 example.com` (2 s timeout)
- A service is marked DOWN after 3 failed checks in a row. It sends an ntfy alert once when that happens, and again when the service is back UP.
- While Proxmox has 3 or more failures in a row, every run sends a Wake-on-LAN magic packet to `d4:5d:64:66:bc:66` (broadcast `192.168.1.255`). It keeps sending until Proxmox answers again.
- Per-service state is kept in `watchdog/state/`, events go to `watchdog/watchdog.log`.
- A lock file stops two runs from overlapping.
- The ntfy topic is read from `watchdog.env`. If it's missing, alerts are skipped and a warning goes to the log.

**battery-guard.sh** (Proxmox host, run by a systemd timer every minute, starting 1 minute after boot)
- Reads battery level, battery status and AC state from `/sys/class/power_supply/`.
- If AC is off (or the battery is discharging) and the battery is at 40% or lower, it:
  1. logs a line to the journal (`battery-guard` tag),
  2. enables Wake-on-LAN on `nic0` with `ethtool -s nic0 wol g`,
  3. suspends the machine with `systemctl suspend`.
- If a battery or AC reading can't be found, it assumes 100% and AC on, so it does nothing.

**homelab-heartbeat.sh** (Proxmox host, run from cron every 2 minutes)
- Out-of-band monitoring agent that collects real-time host and container health metrics:
  - Proxmox node uptime, RAM usage, and root disk consumption.
  - LXC container states: CT 100 (Beeper), CT 101 (AdGuard Home), and CT 102 (Music Brain).
  - Ping reachability to the Raspberry Pi watchdog node (`100.114.251.120`).
- Dispatches an HTTP POST heartbeat to [healthchecks.io](https://healthchecks.io) using credentials safely loaded from `/etc/homelab/heartbeat.env` (`chmod 600`).
- Acts as a dead man's switch: if the host drops power or connectivity, Healthchecks.io triggers push alerts to mobile devices via Matrix / Beeper within 3 minutes.

**music-brain** (CT 102, losslessly manages, syncs and streams personal music library)
- **8-Hour YouTube Music Intelligence Sync**: Runs automatically via `music-history-sync.timer` (00:00, 08:00, 16:00 UTC). Authenticates with Google session cookies, downloads freshly played songs in authentic high-bitrate Opus with verified square covers, and auto-refreshes dynamic playlists (`Recently Played` and `Heavy Rotation`).
- **Strict Lossless FLAC Upgrader**: Background daemon (`music-flac-upgrader.service`) that matches clean music names on Soulseek (slskd), requiring simultaneous verification of clean song title, primary artist, and decoded duration (±3.0s) before replacing Opus. Retains 100% authentic embedded artwork.
- **Headless Metadata Enricher**: Uses Chromaprint (`fpcalc`) and direct MusicBrainz API (`musicbrainz_fingerprinter.py`) to tag official recording IDs, release dates, and genres without touching cover art.
- **Audio Quality Tiering & Video Rip Cleaner**: Automated engine (`video_rip_cleaner.py`) that strips video noise patterns `(Official Music Video)`, verifies audio duration against YouTube Music studio masters, and replaces substandard video audio with studio quality releases.
- **Deduplication Ingestion Guard**: Permanent engine (`dedup_guard.py`) providing fuzzy title normalization and ±4.5s duration checks to prevent duplicate tracks across singles and album releases.
- **Multi-Protocol Streaming & Storage**:
  - **Navidrome**: Subsonic API on port `4533` for Feishin (desktop) and Symfonium (mobile), integrated with official Last.fm API keys for Track Radio (`getSimilarSongs`).
  - **Samba (SMB)**: Network share `\\192.168.1.102\music` mapped as Windows network drive `Z:\music`.

## Power cut sequence

1. Mains goes off. The laptop keeps running on its battery.
2. Battery reaches 40%. battery-guard enables WoL and suspends the laptop.
3. The Pi sees Proxmox stop answering. After 3 failed checks it sends a DOWN alert and starts sending magic packets.
4. Healthchecks.io misses the 2-minute heartbeat ping and dispatches an instant push alert to Beeper/Matrix.
5. Mains returns. The next magic packet wakes the laptop from sleep.
6. Once Proxmox answers again, the Pi sends an UP alert, and Healthchecks.io sends a recovery notification.

## Setup

Pi:

```bash
git clone https://github.com/Ihsan01i/homelab1.git
cd homelab1/watchdog
cp watchdog.env.example watchdog.env
nano watchdog.env        # set your ntfy topic
chmod +x install-watchdog.sh
./install-watchdog.sh
```

Proxmox host:

```bash
git clone https://github.com/Ihsan01i/homelab1.git
cd homelab1/proxmox

# 1. Install Battery Guard (UPS failsafe)
chmod +x install-battery-guard.sh
./install-battery-guard.sh

# 2. Install Healthchecks Dead Man's Switch
chmod +x install-heartbeat.sh
./install-heartbeat.sh
nano /etc/homelab/heartbeat.env   # add your private Healthchecks.io ping URL
```

Music Brain (CT 102 LXC):

```bash
git clone https://github.com/Ihsan01i/homelab1.git
cd homelab1/music-brain
chmod +x setup.sh
./setup.sh
```

Configure:
- `/etc/slskd/slskd.yml`: Put your Soulseek username & password.
- `/opt/music-brain/browser.json`: Put your YouTube Music session headers.

## Known limits

- Wake-on-LAN works from sleep. It does not work from a full shutdown, even though Linux shows WoL as enabled on `nic0`. That's why battery-guard suspends instead of shutting down.
- The Pi sends magic packets any time Proxmox is down, not only after a power cut. If I shut the laptop down or suspend it on purpose, the Pi will try to wake it.
- `ethtool` WoL is set only at the moment battery-guard suspends. It isn't set permanently at boot.
- If the laptop wakes while still on battery at 40% or lower, battery-guard will suspend it again on its next run.

---

## 🚨 Healthchecks.io Dead Man's Switch (Proxmox Alert System)

An external, out-of-band monitoring agent runs directly on the Proxmox VE host (`pve`) every 2 minutes via cron. It collects real-time system metrics and sends an authenticated ping to [healthchecks.io](https://healthchecks.io).

### How It Works

1. **Telemetry Harvest:** Every 120 seconds, `homelab-heartbeat.sh` gathers:
   - Host uptime, RAM usage, and root disk consumption.
   - Container states for CT 100 (Beeper bridge), CT 101 (AdGuard Home), and CT 102 (Music Brain).
   - Tailscale ping reachability to the Raspberry Pi watchdog node (`100.114.251.120`).
2. **Ping Dispatch:** Dispatches an HTTP POST payload to Healthchecks.io using a private check URL stored in `/etc/homelab/heartbeat.env` (`chmod 600`).
3. **Outage Alert Routing:** If the homelab loses power, internet connectivity, or freezes:
   - Healthchecks.io detects the missing check-in.
   - After the grace period (3 minutes), Healthchecks dispatches an automated webhook alert directly to a dedicated Matrix room.
   - Beeper delivers the push notification immediately to mobile devices.
4. **Recovery Notification:** When power or network restores and the first heartbeat arrives, Healthchecks sends an instant recovery notification.

### Secure Installation

```bash
cd proxmox/
sudo chmod +x install-heartbeat.sh
sudo ./install-heartbeat.sh
```

Edit `/etc/homelab/heartbeat.env` (kept strictly out of source control with `chmod 600`) to supply your private Healthchecks.io check URL:
```bash
HC_URL="https://hc-ping.com/YOUR-CHECK-UUID-HERE"
```

---

## 📜 Homelab Infrastructure Operations & Maintenance Log

| Timestamp | Component | Level | Summary |
|---|---|---|---|
| 2026-11-17 18:05 | `HEALTHCHECKS` | **SUCCESS** | Deployed Dead Man's Switch alert system (`homelab-heartbeat.sh`) on Proxmox host with secured `/etc/homelab/heartbeat.env` (`chmod 600`) and 2m cron interval. Webhook tied to Matrix/Beeper. |
| 2026-11-16 15:30 | `PVE_GPU` | **DIAGNOSTIC** | Investigated discrete GPU hardware lock on ASUS TUF FX505DT: identified `nouveau` D3cold-to-D0 PCIe bridge (`00:01.1`) timeout bug and Asus EC fan runaway failsafe (2,800 RPM). |
| 2026-11-13 19:25 | `CONTAINERS` | **MAINTENANCE** | Reconciled LXC state across cluster: verified AdGuard Home (CT 101), Beeper bridge (CT 100), and Music Brain (CT 102) container health and static network assignments. |
| 2026-11-13 14:55 | `POWER_FAILSAFE`| **VERIFIED** | Real-world power outage test: Healthchecks.io detected missing ping at 14:50 and alerted via Beeper within 3m. Laptop battery guard maintained graceful state. |
| 2026-11-03 10:15 | `WATCHDOG` | **SUCCESS** | Raspberry Pi 3B+ watchdog initialized: probes Proxmox HTTPS and AdGuard DNS, equipped with Wake-on-LAN magic packet dispatcher on `nic0`. |
| 2026-10-27 23:05 | `BATTERY_GUARD`| **SUCCESS** | Installed `battery-guard.sh` systemd service on Proxmox host: monitors AC state, auto-suspends at ≤40% battery discharge to prevent battery wear, arms WoL. |
