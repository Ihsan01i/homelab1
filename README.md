# Self-Healing Homelab Architecture

A low-power, high-availability homelab running on repurposed hardware with automated battery management, watchdog health checks, and automatic Wake-on-LAN recovery.

---

## Hardware & Network Overview

| Node | Device | Role | IP Address |
| :--- | :--- | :--- | :--- |
| **Proxmox VE** | ASUS TUF FX505DT | Hypervisor & Compute | \`192.168.1.100\` |
| **AdGuard Home** | Proxmox LXC Container | Local DNS & Ad-blocking | \`192.168.1.210\` |
| **Watchdog** | Raspberry Pi 3B+ | Monitor, WOL Master & Tailscale Node | \`192.168.1.201\` |

---

## Architecture & Lifecycle

\`\`\`
[Normal Operation]
  Mains AC ON ---> Proxmox Node (192.168.1.100) running continuously (~10-15W)
               ---> Pi Watchdog polls HTTPS & DNS every 60s via cron

[Power Outage]
  Mains AC OFF ---> ASUS laptop battery acts as an automated UPS
               ---> Short outage (<2h): Server stays 100% online, no interruption
               ---> Extended blackout: Battery drains to 40%
               ---> battery-guard.sh arms Wake-on-LAN and calls 'systemctl suspend'
               ---> Laptop enters S3 sleep, drawing ~1W (NIC remains energized)

[Recovery]
  Mains AC Restored ---> Raspberry Pi boots/runs watchdog
                    ---> Detects Proxmox is DOWN (3 consecutive checks)
                    ---> Pi fires Wake-on-LAN magic packet to d4:5d:64:66:bc:66
                    ---> Laptop wakes from S3, Proxmox boots, AdGuard resumes
                    ---> Watchdog verifies HTTPS is UP and sends ntfy recovery alert
\`\`\`

---

## Setup Instructions

### 1. Raspberry Pi (Watchdog)
\`\`\`bash
git clone https://github.com/Ihsan01i/homelab1.git
cd homelab1/watchdog
cp watchdog.env.example watchdog.env
# Edit watchdog.env with your private ntfy topic:
nano watchdog.env

chmod +x install-watchdog.sh
./install-watchdog.sh
\`\`\`

### 2. Proxmox VE (Host)
\`\`\`bash
git clone https://github.com/Ihsan01i/homelab1.git
cd homelab1/proxmox
chmod +x install-battery-guard.sh
./install-battery-guard.sh
\`\`\`
