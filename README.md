# Homelab

A personal homelab for learning Linux system administration, networking, virtualization, self-hosted services, and infrastructure management.

This repository documents my homelab journey, from running services on standalone Ubuntu Server machines to consolidating services with Proxmox VE and LXC containers.

## Goals

- Learn Linux administration and server management.
- Understand networking, DNS, DHCP, and network-wide ad blocking.
- Experiment with virtualization and Linux containers.
- Deploy and maintain self-hosted services.
- Document configurations, troubleshooting steps, and lessons learned.
- Build practical experience with IT infrastructure and systems engineering.

---

## Current Infrastructure

### Proxmox VE

| Property | Details |
|---|---|
| Host hardware | ASUS TUF FX505DT laptop |
| Hypervisor | Proxmox VE |
| Hostname | `pve` |
| LAN IP | `192.168.1.100` |
| Remote access | Tailscale |
| Virtualization | Linux Containers (LXC) |

### Network Overview

| Device / Service | IP Address | Role |
|---|---|---|
| GX Titanium-2122A router | `192.168.1.1` | Router and default gateway |
| Proxmox host (`pve`) | `192.168.1.100` | Virtualization host |
| AdGuard Home | `192.168.1.210` | DNS filtering and network-wide ad blocking |
| Beeper bridge | Container 100 | Beeper bridge service |
| AdGuard Home | Container 101 | DNS filtering service |

*The container IDs are Proxmox IDs, not IP addresses.*

---

## Current Services

### 1. AdGuard Home

**Purpose:** Network-wide DNS filtering and ad blocking.

- Runs in a Debian 13 LXC container.
- Static IP address: `192.168.1.210`.
- Web setup interface: `http://192.168.1.210:3000` (initial setup).
- Configured as the DNS server through the router's LAN DNS settings.
- Uses multiple DNS blocklists to filter known advertising and tracking domains.

**Notes:**
- DNS filtering works across devices configured to use AdGuard Home.
- Not every advertisement can be blocked at the DNS level.
- Some services require certain domains to remain accessible to function correctly.

### 2. Beeper Bridge

**Purpose:** Run the Beeper bridge service in a separate container.

- Runs in a Debian 13 LXC container.
- Proxmox container ID: `100`.

*Additional deployment details and configuration notes will be documented as the setup evolves.*

---

## Previous Infrastructure

Before moving to Proxmox, I experimented with a multi-machine Ubuntu Server setup.

### Dell G3 3579

Previous IP address: `192.168.1.31`

Services deployed during the earlier setup:

- **Pi-hole** — DNS filtering and network-wide ad blocking.
- **Nginx Proxy Manager** — reverse proxy and local service access.
- **Uptime Kuma** — service availability monitoring.
- **Portainer** — Docker container management.
- **Prometheus and Node Exporter** — metrics collection.
- **Grafana** — infrastructure monitoring dashboards.
- **Tailscale** — private remote access.

### ASUS TUF FX505DT

Previous IP address: `192.168.1.32`

- Ubuntu Server.
- Docker.
- Node Exporter.
- Uptime Kuma.

These details describe the previous configuration, not the current Proxmox deployment. The earlier IP addresses may no longer be in use.

---

## Documentation and Maintenance

As the homelab grows, this repository will document:

- Installation and configuration procedures.
- Network and DNS changes.
- Container resource allocations.
- Service management and troubleshooting.
- Configuration changes and lessons learned.
- Future self-hosted services and experiments.

The aim is to keep the documentation practical enough that I can understand and rebuild a service without having to rediscover every step.

## Security Notes

- Never commit passwords, API keys, access tokens, private SSH keys, or `.env` files.
- Review configuration exports before publishing them.
- Avoid publishing sensitive network information or service credentials.
- Sanitize internal details before sharing configuration publicly.

---

## Progress Log

### Previous Setup — June 2026

- Installed Ubuntu Server on the Dell G3 and configured SSH access.
- Installed Docker and deployed Nginx Proxy Manager and Uptime Kuma.
- Added Portainer for Docker management.
- Deployed Pi-hole and configured the router to use it for DNS.
- Configured local `.lan` domains.
- Deployed Prometheus, Node Exporter, and Grafana.
- Configured Tailscale for remote access.
- Set up the ASUS TUF as a secondary monitoring node.

### Current Setup — October 2026

- Set up Proxmox VE on the ASUS TUF FX505DT.
- Deployed a Debian 13 LXC container for the Beeper bridge (CT 100).
- Deployed a Debian 13 LXC container for AdGuard Home (CT 101).
- Configured AdGuard Home with a static IP address.
- Configured the router's LAN DNS settings to use AdGuard Home.
- Verified DNS filtering and observed queries from devices on the network.

---

*This is an evolving project. Documentation will be updated as the homelab changes.*
