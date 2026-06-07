# Home Lab
A personal infrastructure lab built for learning system administration, 
networking, and self-hosted services.

## Network Overview
| Device            | OS               | IP             | Role              |
|-------------------|------------------|----------------|-------------------|
| Titanium-2122A    | Router firmware  | 192.168.1.1    | Router / Gateway  |
| Dell G3 3579      | Ubuntu Server    | 192.168.1.31   | Primary server    |
| ASUS TUF FX505DT  | Ubuntu Server    | 192.168.1.32   | Secondary server  |
| HP 15-bs0xx       | TBD              | TBD            | Server            |
| Raspberry Pi 3B+  | TBD              | TBD            | Lightweight node  |

## Goals
- Run self-hosted services across multiple machines
- Learn networking, server management, and automation
- Document everything as I build it

## What's Running

### Dell G3 (192.168.1.31)
- **Nginx Proxy Manager** — reverse proxy for all services (nginx.lan)
- **Uptime Kuma** — service monitoring dashboard (uptime.lan)
- **Portainer** — Docker container management (docker.lan)
- **Pi-hole** — local DNS server and ad blocker (pi.lan)
- **Prometheus + Node Exporter** — metrics collection
- **Grafana** — monitoring dashboard, accessible remotely via Tailscale (grafana.lan)
- **Tailscale** — secure remote access

### ASUS TUF FX505DT (192.168.1.32)
- **Node Exporter** — metrics collection, monitored by Grafana on Dell
- **Uptime Kuma** — monitored by Dell's Uptime Kuma

## Progress Log
- [2026-06-04] Installed Ubuntu Server on Dell G3, configured SSH access
- [2026-06-04] Installed Docker, deployed Nginx Proxy Manager and Uptime Kuma
- [2026-06-04] Added Portainer for Docker container management
- [2026-06-04] Deployed Pi-hole as local DNS and ad blocker, configured router to use it
- [2026-06-04] Set up .lan domains via Nginx Proxy Manager and Pi-hole local DNS
- [2026-06-05] Fixed Pi-hole DNS by switching to host network mode, confirmed network-wide ad blocking working
- [2026-06-05] Deployed Prometheus + Node Exporter + Grafana monitoring stack
- [2026-06-05] Imported Node Exporter Full dashboard (ID 1860), real-time CPU/RAM/disk/network metrics visible
- [2026-06-05] Set up Tailscale for secure remote access, Grafana accessible remotely
- [2026-06-06] Installed Ubuntu Server on ASUS TUF, configured static IP (192.168.1.32)
- [2026-06-06] Blacklisted nouveau GPU driver, resolved kernel error spam
- [2026-06-06] Installed Docker on ASUS, deployed Node Exporter and Uptime Kuma
- [2026-06-06] Added ASUS to Prometheus monitoring, visible in Grafana dashboard