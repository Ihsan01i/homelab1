# Home Lab

A personal infrastructure lab built for learning system administration, 
networking, and self-hosted services.

## Network Overview

| Device            | OS               | IP             | Role              |
|-------------------|------------------|----------------|-------------------|
| Titanium-2122A    | Router firmware  | 192.168.1.1    | Router / Gateway  |
| Dell G3 3579      | Ubuntu Server    | 192.168.1.31   | Primary server    |
| ASUS TUF FX505DT  | TBD              | TBD            | Server            |
| HP 15-bs0xx       | TBD              | TBD            | Server            |
| Raspberry Pi 3B+  | TBD              | TBD            | Lightweight node  |


## Goals
- Run self-hosted services across multiple machines
- Learn networking, server management, and automation
- Document everything as I build it

## What's Running

### Dell G3 (192.168.1.31)
- **Nginx Proxy Manager** — reverse proxy for all services (port 81)
- **Uptime Kuma** — service monitoring dashboard (port 3001)

## Progress Log
- [2026-06-04] Installed Ubuntu Server on Dell G3, configured SSH access
- [2026-06-04] Installed Docker, deployed Nginx Proxy Manager and Uptime Kuma
