# Homelab1

Scripts and notes for my home server. Proxmox runs on an old laptop, a Raspberry Pi watches it, the laptop battery acts as a small UPS, and dedicated LXC containers run network services and a personal lossless music vault.

## Hardware

| Device | Role | IP |
|---|---|---|
| ASUS TUF FX505DT | Proxmox VE host | 192.168.1.100 |
| LXC container on Proxmox | AdGuard Home (DNS) | 192.168.1.210 |
| LXC container on Proxmox | Music Brain (Lossless Vault & Navidrome) | 192.168.1.102 |
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

**music-brain** (CT 102, losslessly manages, syncs and streams personal music library)
- **8-Hour YouTube Music Intelligence Sync**: Runs automatically via `music-history-sync.timer` (00:00, 08:00, 16:00 UTC). Authenticates with Google session cookies, downloads freshly played songs in authentic high-bitrate Opus with verified square covers, and auto-refreshes dynamic playlists (`Recently Played` and `Heavy Rotation`).
- **Strict Lossless FLAC Upgrader**: Background daemon (`music-flac-upgrader.service`) that matches clean music names on Soulseek (slskd), requiring simultaneous verification of clean song title, primary artist, and decoded duration (±3.0s) before replacing Opus. Retains 100% authentic embedded artwork.
- **Headless Metadata Enricher**: Uses Chromaprint (`fpcalc`) and direct MusicBrainz API (`musicbrainz_fingerprinter.py`) to tag official recording IDs, release dates, and genres without touching cover art.
- **Streaming & Storage**: Navidrome server (port 4533) streams Subsonic audio to Feishin (Windows) and BitChord (Android). Local vault is accessible via Samba share `\\192.168.1.102\music` mapped as `Z:\music`.

## Power cut sequence

1. Mains goes off. The laptop keeps running on its battery.
2. Battery reaches 40%. battery-guard enables WoL and suspends the laptop.
3. The Pi sees Proxmox stop answering. After 3 failed checks it sends a DOWN alert and starts sending magic packets.
4. Mains returns. The next magic packet wakes the laptop from sleep.
5. Once Proxmox answers again, the Pi sends an UP alert.

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
chmod +x install-battery-guard.sh
./install-battery-guard.sh
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
