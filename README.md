# NetForge

A cross-platform network management toolkit with a native dark GUI.

NetForge bundles the utilities you reach for during a network problem — proxy control, latency probing, DNS inspection, port scanning, throughput testing, and bandwidth monitoring — into a single window. It runs on Windows, macOS, and Linux, requires nothing outside the Python standard library, and never shells out to a browser or a web dashboard.

[![Python](https://img.shields.io/badge/python-3.8%2B-3776ab?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-4c8dff?style=flat-square)](#platform-support)
[![Dependencies](https://img.shields.io/badge/dependencies-none-3ddc97?style=flat-square)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-8b95a5?style=flat-square)](#license)

---

## Features

| Module | Description |
| --- | --- |
| **Dashboard** | Hostname, OS, local IP, MAC, default gateway, DNS servers, public IP, and proxy state at a glance, plus a live table of every network interface. |
| **Proxy** | Reads and writes the system-wide proxy configuration. Includes a bypass list editor, an environment-variable viewer, and a connectivity test that routes through the configured proxy. |
| **Latency Monitor** | Continuous ICMP probing with a scrolling response-time graph, running packet-loss counter, and min / avg / max statistics. |
| **DNS Toolkit** | Record lookups (A, AAAA, NS, CNAME, PTR, MX, TXT) via the system resolver and a direct query against a public resolver, plus a benchmark of eight public DNS providers. |
| **Port Scanner** | Multithreaded TCP connect scan with service-name identification, lightweight banner grabbing, progress reporting, and preset port sets for common scenarios. |
| **Speed Test** | Download and upload throughput measured against Cloudflare's public endpoints, with live progress and idle-latency sampling. |
| **Bandwidth Monitor** | Real-time per-interface upload and download rates with a throughput history graph and session totals. |
| **Tools** | Subnet calculator with usable-host math and reverse-DNS zone, Wake-on-LAN magic packet sender, and a traceroute tab. |

---

## Requirements

- **Python 3.8 or newer**
- **Tkinter** — bundled with the standard Windows and macOS installers; on Linux it may be a separate package
- **psutil** — *optional*. When installed, it unlocks richer interface enumeration and precise bandwidth counters. Without it, NetForge falls back to `/proc/net/dev` on Linux and reports reduced detail elsewhere.

No third-party packages are required for core functionality.

---

## Installation

```bash
git clone https://github.com/<your-username>/netforge.git
cd netforge
python netforge.py
