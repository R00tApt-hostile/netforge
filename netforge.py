#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetForge — Network Management Toolkit
=====================================
A cross-platform (Windows / macOS / Linux) desktop toolkit with a sleek dark GUI:

  • Dashboard      – live host, interface, gateway, DNS and public-IP overview
  • Proxy          – read/apply system proxy (registry / gsettings / networksetup)
  • Latency        – continuous ping with live sparkline graph
  • DNS            – record lookup + public resolver benchmark (raw UDP DNS)
  • Ports          – multithreaded TCP port scanner with presets
  • Speed          – download / upload throughput test (Cloudflare endpoints)
  • Monitor        – live per-interface bandwidth graph
  • Tools          – subnet calculator, Wake-on-LAN, traceroute

Only the standard library is required. `psutil` is optional and, when present,
unlocks richer interface / bandwidth data.

Run:  python netforge.py
"""

from __future__ import annotations

import concurrent.futures
import ctypes
import ipaddress
import json
import os
import platform
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

# ─────────────────────────────────────────────────────────────────────────────
#  Platform primitives
# ─────────────────────────────────────────────────────────────────────────────

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

FONT = {"Windows": "Segoe UI", "Darwin": "Helvetica Neue"}.get(platform.system(), "DejaVu Sans")
MONO = {"Windows": "Consolas", "Darwin": "Menlo"}.get(platform.system(), "DejaVu Sans Mono")


def run(cmd, timeout=10, shell=False) -> str:
    """Run a command, return combined stdout+stderr (never raises)."""
    try:
        p = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True,
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def have(prog: str) -> bool:
    return shutil.which(prog) is not None


# ─────────────────────────────────────────────────────────────────────────────
#  Theme
# ─────────────────────────────────────────────────────────────────────────────

class C:
    BG       = "#0f1115"
    PANEL    = "#161a21"
    PANEL_2  = "#1c2129"
    PANEL_3  = "#232935"
    BORDER   = "#252b36"
    TEXT     = "#e6e9ef"
    MUTED    = "#8b95a5"
    DIM      = "#5d6675"
    ACCENT   = "#4c8dff"
    ACCENT_2 = "#6ea8ff"
    GREEN    = "#3ddc97"
    AMBER    = "#ffb454"
    RED      = "#ff5c7c"
    PURPLE   = "#b48cff"


# ─────────────────────────────────────────────────────────────────────────────
#  Reusable widgets
# ─────────────────────────────────────────────────────────────────────────────

class Btn(tk.Frame):
    """Flat, themeable button that renders identically on every platform."""
    PALETTE = {
        "primary": (C.ACCENT, "#ffffff", C.ACCENT_2),
        "ghost":   (C.PANEL_3, C.TEXT, "#2c3442"),
        "danger":  (C.RED, "#2a0a12", "#ff7d95"),
        "success": (C.GREEN, "#05271b", "#5eeab1"),
        "warn":    (C.AMBER, "#2a1c05", "#ffc978"),
    }

    def __init__(self, parent, text, command, kind="primary", pad=(16, 8), size=10):
        bg, fg, hov = self.PALETTE[kind]
        super().__init__(parent, bg=bg, cursor="hand2")
        self._bg, self._hov = bg, hov
        self.lbl = tk.Label(self, text=text, bg=bg, fg=fg,
                            font=(FONT, size, "bold"))
        self.lbl.pack(padx=pad[0], pady=pad[1])
        self.command = command
        for w in (self, self.lbl):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _enter(self, _=None):
        self.configure(bg=self._hov)
        self.lbl.configure(bg=self._hov)

    def _leave(self, _=None):
        self.configure(bg=self._bg)
        self.lbl.configure(bg=self._bg)

    def set_text(self, t):
        self.lbl.configure(text=t)

    def set_kind(self, kind):
        self._bg, fg, self._hov = self.PALETTE[kind]
        self.configure(bg=self._bg)
        self.lbl.configure(bg=self._bg, fg=fg)


class Card(tk.Frame):
    """Bordered panel with an optional caption."""
    def __init__(self, parent, title=None, **kw):
        super().__init__(parent, bg=C.PANEL,
                         highlightbackground=C.BORDER, highlightthickness=1, **kw)
        self.inner = tk.Frame(self, bg=C.PANEL)
        if title:
            tk.Label(self, text=title.upper(), bg=C.PANEL, fg=C.MUTED,
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=16, pady=(13, 0))
        self.inner.pack(fill="both", expand=True, padx=16, pady=14)


def make_entry(parent, textvariable=None, width=26, mono=False):
    e = tk.Entry(parent, textvariable=textvariable, width=width,
                 bg=C.PANEL_2, fg=C.TEXT, insertbackground=C.ACCENT,
                 relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=C.BORDER, highlightcolor=C.ACCENT,
                 font=(MONO if mono else FONT, 10))
    return e


def field(parent, label, var=None, width=26, mono=False):
    """A labelled entry, packed vertically. Returns the Entry."""
    wrap = tk.Frame(parent, bg=C.PANEL)
    tk.Label(wrap, text=label, bg=C.PANEL, fg=C.MUTED,
             font=(FONT, 9)).pack(anchor="w", pady=(0, 3))
    e = make_entry(wrap, var, width, mono)
    e.pack(ipady=6, fill="x")
    wrap.pack(fill="x", pady=(0, 10))
    return e


def kv_row(parent, key, value, value_color=None):
    row = tk.Frame(parent, bg=C.PANEL)
    tk.Label(row, text=key, bg=C.PANEL, fg=C.MUTED, font=(FONT, 9),
             width=18, anchor="w").pack(side="left")
    lbl = tk.Label(row, text=value, bg=C.PANEL,
                   fg=value_color or C.TEXT, font=(MONO, 9), anchor="w",
                   justify="left")
    lbl.pack(side="left", fill="x", expand=True)
    row.pack(fill="x", pady=2)
    return lbl


class Sparkline(tk.Canvas):
    """Scrolling line graph drawn on a plain canvas."""
    def __init__(self, parent, height=130, max_points=120, color=C.ACCENT, **kw):
        super().__init__(parent, bg=C.PANEL, highlightthickness=0,
                         height=height, **kw)
        self.data = []
        self.max_points = max_points
        self.color = color
        self.bind("<Configure>", lambda e: self.redraw())

    def push(self, v):
        self.data.append(v)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        self.redraw()

    def clear(self):
        self.data.clear()
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        for i in range(1, 4):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=C.BORDER)
        if len(self.data) < 2:
            self.create_text(w / 2, h / 2, text="waiting for data…",
                             fill=C.DIM, font=(FONT, 9))
            return
        mx = max(max(self.data), 0.001)
        n = len(self.data)
        pts = []
        for i, v in enumerate(self.data):
            x = w * i / (n - 1)
            y = h - 8 - (v / mx) * (h - 22)
            pts.extend([x, y])
        self.create_polygon(pts + [w, h, 0, h], fill=C.PANEL_2, outline="")
        self.create_line(pts, fill=self.color, width=2, smooth=True)
        self.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3,
                         fill=self.color, outline="")


# ─────────────────────────────────────────────────────────────────────────────
#  Network information helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_mac() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> i) & 0xFF:02x}" for i in range(40, -1, -8))


def get_gateway() -> str:
    try:
        if IS_WIN:
            m = re.search(r"Default Gateway[^:]*:\s*([0-9a-fA-F:.]+)", run(["ipconfig"]))
            if m:
                return m.group(1)
        elif IS_MAC:
            for ln in run(["netstat", "-rn"]).splitlines():
                if ln.startswith("default"):
                    parts = ln.split()
                    if len(parts) > 1:
                        return parts[1]
        else:
            m = re.search(r"default via ([0-9a-fA-F:.]+)", run(["ip", "route"]))
            if m:
                return m.group(1)
            for ln in run(["route", "-n"]).splitlines():
                if ln.startswith("0.0.0.0"):
                    parts = ln.split()
                    if len(parts) > 1:
                        return parts[1]
    except Exception:
        pass
    return "—"


def get_dns_servers() -> list[str]:
    out = []
    try:
        if IS_WIN:
            lines = run(["ipconfig", "/all"]).splitlines()
            for i, ln in enumerate(lines):
                if "DNS Servers" in ln:
                    m = re.search(r":\s*([0-9a-fA-F:.]+)", ln)
                    if m:
                        out.append(m.group(1))
                    j = i + 1
                    while j < len(lines) and ":" not in lines[j] and lines[j].strip():
                        m2 = re.match(r"\s+([0-9a-fA-F:.]+)\s*$", lines[j])
                        if m2:
                            out.append(m2.group(1))
                        j += 1
        elif IS_MAC:
            out = re.findall(r"nameserver\[\d+\]\s*:\s*([0-9a-fA-F:.]+)",
                             run(["scutil", "--dns"]))
        else:
            if have("resolvectl"):
                txt = run(["resolvectl", "status"])
                out = re.findall(r"Current DNS Server:\s*([0-9a-fA-F:.]+)", txt)
                if not out:
                    out = re.findall(r"DNS Servers?:\s*([0-9a-fA-F:.]+)", txt)
            if not out:
                try:
                    with open("/etc/resolv.conf") as f:
                        out = re.findall(r"^nameserver\s+([0-9a-fA-F:.]+)",
                                         f.read(), re.M)
                except Exception:
                    pass
    except Exception:
        pass
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def get_interfaces() -> list[dict]:
    """List of {name, ip, mac, status, speed}."""
    res = []
    try:
        import psutil  # type: ignore
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for name, alist in addrs.items():
            ip4, mac = "", ""
            for a in alist:
                fam = getattr(a, "family", None)
                if fam == socket.AF_INET:
                    ip4 = a.address
                elif fam == getattr(psutil, "AF_LINK", object()) or fam == 17:
                    mac = a.address
            st = stats.get(name)
            res.append({
                "name": name,
                "ip": ip4 or "—",
                "mac": mac or "—",
                "status": "Up" if (st and st.isup) else "Down",
                "speed": f"{st.speed} Mbps" if (st and st.speed) else "—",
            })
        return res
    except Exception:
        pass

    # ── fallbacks without psutil ────────────────────────────────────────────
    try:
        if IS_LINUX:
            for ln in run(["ip", "-o", "addr", "show"]).splitlines():
                parts = ln.split()
                if len(parts) > 3 and parts[2] == "inet":
                    name = parts[1]
                    entry = next((r for r in res if r["name"] == name), None)
                    if entry is None:
                        entry = {"name": name, "ip": "—", "mac": "—",
                                 "status": "Up", "speed": "—"}
                        res.append(entry)
                    entry["ip"] = parts[3].split("/")[0]
            for ln in run(["ip", "-o", "link", "show"]).splitlines():
                m = re.match(r"\d+:\s+(\S+):.*link/ether\s+(\S+)", ln)
                if m:
                    entry = next((r for r in res if r["name"] == m.group(1)), None)
                    if entry:
                        entry["mac"] = m.group(2)
        elif IS_MAC:
            cur = None
            for ln in run(["ifconfig"]).splitlines():
                m = re.match(r"^(\w+):", ln)
                if m:
                    cur = {"name": m.group(1), "ip": "—", "mac": "—",
                           "status": "Down", "speed": "—"}
                    res.append(cur)
                elif cur is not None:
                    m2 = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ln)
                    if m2:
                        cur["ip"] = m2.group(1)
                    m3 = re.search(r"ether ([0-9a-f:]+)", ln)
                    if m3:
                        cur["mac"] = m3.group(1)
                    if "status: active" in ln:
                        cur["status"] = "Up"
        elif IS_WIN:
            txt = run(["ipconfig", "/all"])
            blocks = re.split(r"\r?\n(?=\S)", txt)
            for b in blocks:
                m = re.search(r"adapter (.+?):", b)
                if not m:
                    continue
                name = m.group(1)
                ip = re.search(r"IPv4 Address[^:]*:\s*([\d.]+)", b)
                mac = re.search(r"Physical Address[^:]*:\s*([0-9A-Fa-f-]+)", b)
                res.append({
                    "name": name,
                    "ip": ip.group(1) if ip else "—",
                    "mac": (mac.group(1).replace("-", ":").lower() if mac else "—"),
                    "status": "Up" if ip else "Down",
                    "speed": "—",
                })
    except Exception:
        pass
    return res


def get_public_ip(timeout=6) -> dict:
    """Return {'ip':..., 'detail':...} best-effort."""
    endpoints = [
        ("https://api.ipify.org?format=json", "ip"),
        ("https://ifconfig.me/all.json", "ip_addr"),
    ]
    for url, key in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NetForge/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            return {"ip": data.get(key, "—"), "detail": "ok"}
        except Exception:
            continue
    return {"ip": "unavailable", "detail": "offline"}


# ─────────────────────────────────────────────────────────────────────────────
#  Proxy backend
# ─────────────────────────────────────────────────────────────────────────────

WIN_PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


class ProxyManager:
    """Reads / writes the OS-level proxy configuration."""

    # ── read ────────────────────────────────────────────────────────────────
    @staticmethod
    def read() -> dict:
        try:
            if IS_WIN:
                return ProxyManager._read_win()
            if IS_MAC:
                return ProxyManager._read_mac()
            return ProxyManager._read_linux()
        except Exception as e:
            return {"enabled": False, "host": "", "port": "", "bypass": "",
                    "backend": f"error: {e}"}

    @staticmethod
    def _read_win() -> dict:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_PROXY_KEY)
        def g(name, default):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return default
        enabled = bool(g("ProxyEnable", 0))
        raw = g("ProxyServer", "") or ""
        bypass = g("ProxyOverride", "") or ""
        s = raw
        if "=" in raw:
            m = re.search(r"(?:https|http)=([^;]+)", raw)
            if m:
                s = m.group(1)
        host, port = s, ""
        if ":" in s:
            host, _, port = s.rpartition(":")
        return {"enabled": enabled, "host": host, "port": port,
                "bypass": bypass, "backend": "Windows Registry (WinINET)"}

    @staticmethod
    def _read_mac() -> dict:
        svc = ProxyManager._mac_service()
        out = run(["networksetup", "-getwebproxy", svc])
        enabled = "Enabled: Yes" in out
        host = (re.search(r"Server:\s*(\S+)", out) or [None, ""])[1]
        port = (re.search(r"Port:\s*(\d+)", out) or [None, ""])[1]
        bypass = run(["networksetup", "-getproxybypassdomains", svc]).strip()
        return {"enabled": enabled, "host": host or "", "port": port or "",
                "bypass": ";".join(bypass.splitlines()),
                "backend": f"macOS networksetup ({svc})"}

    @staticmethod
    def _read_linux() -> dict:
        if have("gsettings"):
            mode = run(["gsettings", "get", "org.gnome.system.proxy",
                        "mode"]).strip().strip("'")
            host = run(["gsettings", "get", "org.gnome.system.proxy.http",
                        "host"]).strip().strip("'")
            port = run(["gsettings", "get", "org.gnome.system.proxy.http",
                        "port"]).strip()
            ignore = run(["gsettings", "get", "org.gnome.system.proxy",
                          "ignore-hosts"]).strip()
            return {"enabled": mode == "manual", "host": host, "port": port,
                    "bypass": ignore.strip("[]").replace("'", ""),
                    "backend": "GNOME gsettings"}
        env = {k: os.environ.get(k, "") for k in
               ("http_proxy", "https_proxy", "no_proxy")}
        return {"enabled": bool(env["http_proxy"]), "host": env["http_proxy"],
                "port": "", "bypass": env["no_proxy"],
                "backend": "Environment variables only"}

    @staticmethod
    def _mac_service() -> str:
        order = run(["networksetup", "-listnetworkserviceorder"])
        m = re.search(r"\(\d+\)\s+(.+)$", order, re.M)
        if m:
            return m.group(1).strip()
        services = run(["networksetup", "-listallnetworkservices"]).splitlines()
        for s in services[1:]:
            if s.strip():
                return s.strip()
        return "Wi-Fi"

    # ── write ───────────────────────────────────────────────────────────────
    @staticmethod
    def apply(enabled: bool, host: str, port: str, bypass: str) -> str:
        try:
            if IS_WIN:
                return ProxyManager._apply_win(enabled, host, port, bypass)
            if IS_MAC:
                return ProxyManager._apply_mac(enabled, host, port, bypass)
            return ProxyManager._apply_linux(enabled, host, port, bypass)
        except Exception as e:
            return f"Failed: {e}"

    @staticmethod
    def _apply_win(enabled, host, port, bypass) -> str:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_PROXY_KEY, 0,
                           winreg.KEY_ALL_ACCESS)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                          1 if enabled else 0)
        if host and port:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                              f"{host}:{port}")
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, bypass)
        winreg.CloseKey(k)
        try:  # notify WinINET so running apps pick it up immediately
            wininet = ctypes.windll.Wininet
            wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
            wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
        except Exception:
            pass
        return "Applied to Windows registry."

    @staticmethod
    def _apply_linux(enabled, host, port, bypass) -> str:
        if not have("gsettings"):
            # Fall back to environment guidance
            return ("gsettings not found. Export manually, e.g.\n"
                    f"  export http_proxy=http://{host}:{port}\n"
                    f"  export https_proxy=http://{host}:{port}")
        run(["gsettings", "set", "org.gnome.system.proxy", "mode",
             "manual" if enabled else "none"])
        if enabled:
            for scheme in ("http", "https"):
                run(["gsettings", "set", f"org.gnome.system.proxy.{scheme}",
                     "host", host])
                run(["gsettings", "set", f"org.gnome.system.proxy.{scheme}",
                     "port", str(port)])
            items = [x.strip() for x in re.split(r"[;,]", bypass) if x.strip()]
            arr = "[" + ", ".join(f"'{x}'" for x in items) + "]"
            run(["gsettings", "set", "org.gnome.system.proxy",
                 "ignore-hosts", arr])
        return "Applied to GNOME proxy settings."

    @staticmethod
    def _apply_mac(enabled, host, port, bypass) -> str:
        svc = ProxyManager._mac_service()
        if enabled:
            run(["networksetup", "-setwebproxy", svc, host, str(port)])
            run(["networksetup", "-setsecurewebproxy", svc, host, str(port)])
            run(["networksetup", "-setwebproxystate", svc, "on"])
            run(["networksetup", "-setsecurewebproxystate", svc, "on"])
            items = [x.strip() for x in re.split(r"[;,]", bypass) if x.strip()]
            if items:
                run(["networksetup", "-setproxybypassdomains", svc, *items])
        else:
            run(["networksetup", "-setwebproxystate", svc, "off"])
            run(["networksetup", "-setsecurewebproxystate", svc, "off"])
        return f"Applied to network service '{svc}'."

    @staticmethod
    def env_vars() -> dict:
        keys = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "no_proxy", "ALL_PROXY"]
        return {k: os.environ[k] for k in keys if os.environ.get(k)}


# ─────────────────────────────────────────────────────────────────────────────
#  Minimal DNS client (no external deps)
# ─────────────────────────────────────────────────────────────────────────────

QTYPE = {"A": 1, "NS": 2, "CNAME": 5, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}


def _read_name(data: bytes, off: int):
    labels, jumps, orig = [], 0, off
    while True:
        if off >= len(data):
            break
        l = data[off]
        if l == 0:
            off += 1
            break
        if l & 0xC0 == 0xC0:
            ptr = ((l & 0x3F) << 8) | data[off + 1]
            if jumps == 0:
                orig = off + 2
            jumps += 1
            off = ptr
            if jumps > 12:
                break
            continue
        labels.append(data[off + 1:off + 1 + l].decode("utf-8", "replace"))
        off += 1 + l
    return ".".join(labels), (orig if jumps else off)


def dns_query(server: str, name: str, qtype: str = "A", timeout=3.0):
    """Return (elapsed_ms, [records], rcode)."""
    qtype_num = QTYPE.get(qtype.upper(), 1)
    tid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", qtype_num, 1)

    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(8192)
    finally:
        s.close()
    elapsed = (time.time() - t0) * 1000

    _, flags, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
    rcode = flags & 0x0F
    off = 12
    for _ in range(qd):
        _, off = _read_name(data, off)
        off += 4

    records = []
    for _ in range(an):
        _, off = _read_name(data, off)
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        off += 10
        rdata = data[off:off + rdlen]
        end = off + rdlen
        if rtype == 1 and rdlen == 4:
            records.append(("A", socket.inet_ntoa(rdata)))
        elif rtype == 28 and rdlen == 16:
            records.append(("AAAA", socket.inet_ntop(socket.AF_INET6, rdata)))
        elif rtype in (5, 2, 12):
            nm, _ = _read_name(data, off)
            records.append(({5: "CNAME", 2: "NS", 12: "PTR"}[rtype], nm))
        elif rtype == 15:
            pref = struct.unpack(">H", rdata[:2])[0]
            nm, _ = _read_name(data, off + 2)
            records.append(("MX", f"{pref} {nm}"))
        elif rtype == 16:
            parts, i = [], 0
            while i < len(rdata):
                ln = rdata[i]
                i += 1
                parts.append(rdata[i:i + ln].decode("utf-8", "replace"))
                i += ln
            records.append(("TXT", "".join(parts)))
        off = end
    return elapsed, records, rcode


PUBLIC_RESOLVERS = [
    ("Cloudflare", "1.1.1.1"),
    ("Cloudflare Alt", "1.0.0.1"),
    ("Google", "8.8.8.8"),
    ("Google Alt", "8.8.4.4"),
    ("Quad9", "9.9.9.9"),
    ("OpenDNS", "208.67.222.222"),
    ("AdGuard", "94.140.14.14"),
    ("Control D", "76.76.2.0"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Base page
# ─────────────────────────────────────────────────────────────────────────────

class Page(tk.Frame):
    title = "Page"
    subtitle = ""

    def __init__(self, parent, app):
        super().__init__(parent, bg=C.BG)
        self.app = app

        head = tk.Frame(self, bg=C.BG)
        head.pack(fill="x", padx=26, pady=(22, 0))
        tk.Label(head, text=self.title, bg=C.BG, fg=C.TEXT,
                 font=(FONT, 19, "bold")).pack(anchor="w")
        if self.subtitle:
            tk.Label(head, text=self.subtitle, bg=C.BG, fg=C.MUTED,
                     font=(FONT, 10)).pack(anchor="w", pady=(2, 0))

        self.body = tk.Frame(self, bg=C.BG)
        self.body.pack(fill="both", expand=True, padx=26, pady=(18, 22))
        self.build()

    def build(self):
        ...

    def on_show(self):
        ...


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard
# ─────────────────────────────────────────────────────────────────────────────

class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Live overview of this machine and its network"

    def build(self):
        self.vars = {}
        tiles = [
            ("Hostname", "hostname"), ("Operating System", "os"),
            ("Local IP", "local_ip"), ("MAC Address", "mac"),
            ("Default Gateway", "gateway"), ("DNS Servers", "dns"),
            ("Public IP", "public_ip"), ("Proxy Status", "proxy"),
        ]

        grid = tk.Frame(self.body, bg=C.BG)
        grid.pack(fill="x")
        for c in range(4):
            grid.columnconfigure(c, weight=1, uniform="tile")

        for i, (caption, key) in enumerate(tiles):
            card = tk.Frame(grid, bg=C.PANEL,
                            highlightbackground=C.BORDER, highlightthickness=1)
            card.grid(row=i // 4, column=i % 4, sticky="nsew",
                      padx=(0 if i % 4 == 0 else 6, 0), pady=6)
            tk.Label(card, text=caption.upper(), bg=C.PANEL, fg=C.MUTED,
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=14,
                                                  pady=(12, 2))
            var = tk.StringVar(value="…")
            tk.Label(card, textvariable=var, bg=C.PANEL, fg=C.TEXT,
                     font=(FONT, 12, "bold"), wraplength=210, justify="left",
                     anchor="w").pack(anchor="w", padx=14, pady=(0, 12))
            self.vars[key] = var

        # Interfaces table
        card = Card(self.body, "Network Interfaces")
        card.pack(fill="both", expand=True, pady=(14, 0))
        cols = ("name", "ip", "mac", "status", "speed")
        self.iface_tree = ttk.Treeview(card.inner, columns=cols,
                                       show="headings", height=5)
        for c, w in zip(cols, (190, 150, 190, 90, 110)):
            self.iface_tree.heading(c, text=c.title())
            self.iface_tree.column(c, width=w, anchor="w")
        self.iface_tree.pack(fill="both", expand=True)

        bar = tk.Frame(self.body, bg=C.BG)
        bar.pack(fill="x", pady=(14, 0))
        Btn(bar, "⟳  Refresh", self.refresh, "primary").pack(side="left")
        self.status = tk.Label(bar, text="", bg=C.BG, fg=C.MUTED,
                               font=(FONT, 9))
        self.status.pack(side="left", padx=14)

    def on_show(self):
        if not self.vars["hostname"].get() not in ("", "…"):
            pass
        self.refresh()

    def refresh(self):
        v = self.vars
        v["hostname"].set(socket.gethostname())
        v["os"].set(f"{platform.system()} {platform.release()}")
        v["local_ip"].set(get_local_ip())
        v["mac"].set(get_mac())
        v["gateway"].set(get_gateway())
        dns = get_dns_servers()
        v["dns"].set("\n".join(dns[:3]) if dns else "—")
        v["proxy"].set("Enabled" if ProxyManager.read().get("enabled") else "Direct")
        v["public_ip"].set("resolving…")
        self.status.configure(text="Refreshing…")

        for row in self.iface_tree.get_children():
            self.iface_tree.delete(row)
        for it in get_interfaces():
            self.iface_tree.insert("", "end", values=(
                it["name"], it["ip"], it["mac"], it["status"], it["speed"]))

        threading.Thread(target=self._fetch_public, daemon=True).start()

    def _fetch_public(self):
        info = get_public_ip()
        self.app.post(lambda: self.vars["public_ip"].set(info["ip"]))
        self.app.post(lambda: self.status.configure(
            text=f"Updated {datetime.now().strftime('%H:%M:%S')}"))


# ─────────────────────────────────────────────────────────────────────────────
#  Proxy page
# ─────────────────────────────────────────────────────────────────────────────

class ProxyPage(Page):
    title = "Proxy Configuration"
    subtitle = "Inspect and apply the system-wide proxy without touching a terminal"

    def build(self):
        self.mode = tk.StringVar(value="off")
        self.host = tk.StringVar()
        self.port = tk.StringVar()
        self.bypass = tk.StringVar(value="localhost;127.*;10.*;172.16.*;192.168.*;<local>")

        wrap = tk.Frame(self.body, bg=C.BG)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=3, uniform="col")
        wrap.columnconfigure(1, weight=2, uniform="col")

        # ── left: config ────────────────────────────────────────────────────
        left = Card(wrap, "System Proxy")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.backend_lbl = tk.Label(left.inner, text="detecting…", bg=C.PANEL,
                                    fg=C.MUTED, font=(FONT, 9))
        self.backend_lbl.pack(anchor="w", pady=(0, 12))

        self.state_lbl = tk.Label(left.inner, text="—", bg=C.PANEL, fg=C.TEXT,
                                  font=(MONO, 10), justify="left", anchor="w")
        self.state_lbl.pack(anchor="w", pady=(0, 16))

        modes = tk.Frame(left.inner, bg=C.PANEL)
        modes.pack(fill="x", pady=(0, 14))
        for val, label in (("off", "Direct connection"),
                           ("manual", "Manual proxy")):
            rb = tk.Radiobutton(modes, text=label, variable=self.mode, value=val,
                                bg=C.PANEL, fg=C.TEXT, selectcolor=C.PANEL_2,
                                activebackground=C.PANEL,
                                activeforeground=C.ACCENT,
                                font=(FONT, 10), anchor="w",
                                highlightthickness=0, bd=0,
                                command=self._sync_enabled)
            rb.pack(anchor="w", pady=2)

        grid = tk.Frame(left.inner, bg=C.PANEL)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=1)

        h_wrap = tk.Frame(grid, bg=C.PANEL)
        h_wrap.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Label(h_wrap, text="Proxy host", bg=C.PANEL, fg=C.MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 3))
        self.host_entry = make_entry(h_wrap, self.host)
        self.host_entry.pack(ipady=6, fill="x")

        p_wrap = tk.Frame(grid, bg=C.PANEL)
        p_wrap.grid(row=0, column=1, sticky="ew")
        tk.Label(p_wrap, text="Port", bg=C.PANEL, fg=C.MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 3))
        self.port_entry = make_entry(p_wrap, self.port, width=8)
        self.port_entry.pack(ipady=6, fill="x")

        b_wrap = tk.Frame(left.inner, bg=C.PANEL)
        b_wrap.pack(fill="x", pady=(14, 0))
        tk.Label(b_wrap, text="Bypass list (semicolon separated)", bg=C.PANEL,
                 fg=C.MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 3))
        make_entry(b_wrap, self.bypass).pack(ipady=6, fill="x")

        actions = tk.Frame(left.inner, bg=C.PANEL)
        actions.pack(fill="x", pady=(18, 0))
        Btn(actions, "Apply", self._apply, "primary").pack(side="left")
        Btn(actions, "Reload", self.refresh, "ghost").pack(side="left", padx=8)
        Btn(actions, "Test via proxy", self._test, "ghost").pack(side="left")

        # ── right: environment + connectivity ───────────────────────────────
        right = tk.Frame(wrap, bg=C.BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        env_card = Card(right, "Environment Variables")
        env_card.pack(fill="x")
        self.env_box = tk.Frame(env_card.inner, bg=C.PANEL)
        self.env_box.pack(fill="x")

        test_card = Card(right, "Connectivity Check")
        test_card.pack(fill="both", expand=True, pady=(14, 0))
        self.test_out = tk.Label(test_card.inner, text="Not run yet.",
                                 bg=C.PANEL, fg=C.MUTED, font=(MONO, 9),
                                 justify="left", anchor="nw", wraplength=330)
        self.test_out.pack(fill="both", expand=True)

        self._sync_enabled()
        self.refresh()

    def _sync_enabled(self):
        state = "normal" if self.mode.get() == "manual" else "disabled"
        for w in (self.host_entry, self.port_entry):
            w.configure(state=state)

    def on_show(self):
        self.refresh()

    def refresh(self):
        st = ProxyManager.read()
        self.backend_lbl.configure(text=f"Backend: {st.get('backend', '?')}")
        if st.get("enabled"):
            self.state_lbl.configure(
                text=f"● ACTIVE   http://{st.get('host','')}:{st.get('port','')}",
                fg=C.GREEN)
            self.mode.set("manual")
        else:
            self.state_lbl.configure(text="○ DIRECT — no system proxy",
                                     fg=C.MUTED)
            self.mode.set("off")
        self.host.set(st.get("host", ""))
        self.port.set(st.get("port", ""))
        if st.get("bypass"):
            self.bypass.set(st["bypass"])
        self._sync_enabled()

        for w in self.env_box.winfo_children():
            w.destroy()
        env = ProxyManager.env_vars()
        if env:
            for k, v in env.items():
                kv_row(self.env_box, k, v, C.PURPLE)
        else:
            tk.Label(self.env_box, text="No proxy environment variables set.",
                     bg=C.PANEL, fg=C.DIM, font=(FONT, 9)).pack(anchor="w")

    def _apply(self):
        enabled = self.mode.get() == "manual"
        host, port = self.host.get().strip(), self.port.get().strip()
        if enabled and (not host or not port):
            messagebox.showwarning("NetForge", "Host and port are required.")
            return
        if enabled and not port.isdigit():
            messagebox.showwarning("NetForge", "Port must be numeric.")
            return
        msg = ProxyManager.apply(enabled, host, port, self.bypass.get().strip())
        self.app.set_status(msg)
        self.refresh()

    def _test(self):
        self.test_out.configure(text="Testing…", fg=C.MUTED)

        def work():
            proxy = None
            if self.mode.get() == "manual" and self.host.get() and self.port.get():
                proxy = f"http://{self.host.get()}:{self.port.get()}"
            handler = urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy} if proxy else {})
            opener = urllib.request.build_opener(handler)
            try:
                t0 = time.time()
                with opener.open("https://api.ipify.org?format=json", timeout=8) as r:
                    data = json.loads(r.read().decode())
                dt = (time.time() - t0) * 1000
                txt = (f"✓ Reachable\n\nRoute      : {'via ' + proxy if proxy else 'direct'}\n"
                       f"Public IP  : {data.get('ip')}\nLatency    : {dt:.0f} ms")
                color = C.GREEN
            except Exception as e:
                txt = f"✗ Failed\n\nRoute : {proxy or 'direct'}\nError : {e}"
                color = C.RED
            self.app.post(lambda: self.test_out.configure(text=txt, fg=color))

        threading.Thread(target=work, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
#  Latency page
# ─────────────────────────────────────────────────────────────────────────────

class LatencyPage(Page):
    title = "Latency Monitor"
    subtitle = "Continuous ICMP probing with a live response graph"

    def build(self):
        self.target = tk.StringVar(value="1.1.1.1")
        self.running = False
        self._stop = threading.Event()
        self.sent = 0
        self.lost = 0

        bar = tk.Frame(self.body, bg=C.BG)
        bar.pack(fill="x")

        tk.Label(bar, text="Host", bg=C.BG, fg=C.MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(0, 8))
        e = make_entry(bar, self.target, width=26)
        e.pack(side="left", ipady=6)
        e.bind("<Return>", lambda ev: self.toggle())

        self.btn = Btn(bar, "▶  Start", self.toggle, "success")
        self.btn.pack(side="left", padx=10)
        Btn(bar, "Clear", self.clear, "ghost").pack(side="left")

        self.stats = tk.Label(bar, text="", bg=C.BG, fg=C.MUTED,
                              font=(MONO, 9))
        self.stats.pack(side="right")

        card = Card(self.body, "Response Time (ms)")
        card.pack(fill="both", expand=True, pady=(14, 0))
        self.graph = Sparkline(card.inner, height=170)
        self.graph.pack(fill="both", expand=True)

        log_card = Card(self.body, "Log")
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log = tk.Text(log_card.inner, height=9, bg=C.PANEL_2, fg=C.TEXT,
                           insertbackground=C.ACCENT, relief="flat", bd=0,
                           font=(MONO, 9), highlightthickness=0, wrap="none")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("ok", foreground=C.GREEN)
        self.log.tag_configure("bad", foreground=C.RED)
        self.log.tag_configure("info", foreground=C.MUTED)
        self.log.configure(state="disabled")

    def on_show(self):
        pass

    def clear(self):
        self.sent = self.lost = 0
        self.graph.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._update_stats()

    def toggle(self):
        if self.running:
            self.running = False
            self._stop.set()
            self.btn.set_text("▶  Start")
            self.btn.set_kind("success")
        else:
            host = self.target.get().strip()
            if not host:
                return
            self.running = True
            self._stop.clear()
            self.btn.set_text("■  Stop")
            self.btn.set_kind("danger")
            threading.Thread(target=self._loop, args=(host,), daemon=True).start()

    def _loop(self, host):
        while not self._stop.is_set():
            rtt = ping_once(host, timeout=2.0)
            self.sent += 1
            if rtt is None:
                self.lost += 1
                self.app.post(self._log, "timeout", "bad")
            else:
                self.app.post(self.graph.push, rtt)
                self.app.post(self._log, f"{rtt:7.2f} ms", "ok")
            self.app.post(self._update_stats)
            self._stop.wait(1.0)

    def _log(self, text, tag):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}]  {text}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_stats(self):
        loss = (self.lost / self.sent * 100) if self.sent else 0
        data = self.graph.data
        avg = sum(data) / len(data) if data else 0
        mn = min(data) if data else 0
        mx = max(data) if data else 0
        self.stats.configure(
            text=f"sent {self.sent}   loss {loss:.0f}%   "
                 f"min {mn:.1f}  avg {avg:.1f}  max {mx:.1f} ms")


def ping_once(host: str, timeout: float = 2.0):
    """Return RTT in ms or None on failure."""
    if IS_WIN:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    elif IS_MAC:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
    out = run(cmd, timeout=timeout + 3)
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out, re.I)
    if m:
        return float(m.group(1))
    if re.search(r"ttl[=\s]", out, re.I):
        return 0.0
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  DNS page
# ─────────────────────────────────────────────────────────────────────────────

class DNSLookupPage(Page):
    title = "DNS Toolkit"
    subtitle = "Record lookups and public resolver benchmarking"

    def build(self):
        self.name = tk.StringVar(value="example.com")
        self.qtype = tk.StringVar(value="A")

        wrap = tk.Frame(self.body, bg=C.BG)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=3, uniform="c")
        wrap.columnconfigure(1, weight=2, uniform="c")

        # ── lookup ──────────────────────────────────────────────────────────
        left = Card(wrap, "Lookup")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        row = tk.Frame(left.inner, bg=C.PANEL)
        row.pack(fill="x")
        e = make_entry(row, self.name, width=30)
        e.pack(side="left", ipady=6, fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.lookup())

        combo = ttk.Combobox(row, textvariable=self.qtype, width=7,
                             values=list(QTYPE.keys()), state="readonly")
        combo.pack(side="left", padx=8)

        Btn(row, "Resolve", self.lookup, "primary").pack(side="left")

        self.result = tk.Text(left.inner, height=14, bg=C.PANEL_2, fg=C.TEXT,
                              relief="flat", bd=0, font=(MONO, 9),
                              highlightthickness=0, wrap="word")
        self.result.pack(fill="both", expand=True, pady=(14, 0))
        self.result.tag_configure("head", foreground=C.ACCENT,
                                  font=(MONO, 9, "bold"))
        self.result.tag_configure("ok", foreground=C.GREEN)
        self.result.tag_configure("muted", foreground=C.MUTED)
        self.result.configure(state="disabled")

        # ── benchmark ───────────────────────────────────────────────────────
        right = Card(wrap, "Resolver Benchmark")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        Btn(right.inner, "▶  Run benchmark", self.benchmark, "success").pack(anchor="w")

        cols = ("resolver", "server", "time", "result")
        self.tree = ttk.Treeview(right.inner, columns=cols, show="headings",
                                 height=10)
        for c, w in zip(cols, (120, 120, 80, 110)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(12, 0))
        self.tree.tag_configure("fast", foreground=C.GREEN)
        self.tree.tag_configure("slow", foreground=C.AMBER)
        self.tree.tag_configure("fail", foreground=C.RED)

    def _write(self, text, tag=None):
        self.result.configure(state="normal")
        self.result.insert("end", text, tag or ())
        self.result.see("end")
        self.result.configure(state="disabled")

    def lookup(self):
        name = self.name.get().strip()
        qtype = self.qtype.get()
        if not name:
            return
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.configure(state="disabled")
        self._write(f"── {qtype} lookup for {name} ──\n\n", "head")

        # system resolver
        try:
            t0 = time.time()
            infos = socket.getaddrinfo(name, None)
            dt = (time.time() - t0) * 1000
            seen = []
            for fam, _, _, _, sockaddr in infos:
                ip = sockaddr[0]
                if ip not in seen:
                    seen.append(ip)
                    label = "IPv6" if fam == socket.AF_INET6 else "IPv4"
                    self._write(f"  {label:<5} {ip}\n", "ok")
            self._write(f"\n  system resolver · {dt:.1f} ms\n", "muted")
        except Exception as e:
            self._write(f"  system resolver failed: {e}\n", "muted")

        # explicit query against primary public resolver
        def work():
            try:
                dt, records, rcode = dns_query("1.1.1.1", name, qtype)
                self.app.post(self._write, "\n── via 1.1.1.1 ──\n", "head")
                if rcode != 0:
                    self.app.post(self._write, f"  rcode {rcode} (no answer)\n", "muted")
                for t, val in records:
                    self.app.post(self._write, f"  {t:<6} {val}\n", "ok")
                self.app.post(self._write, f"\n  {dt:.1f} ms\n", "muted")
            except Exception as e:
                self.app.post(self._write, f"\n  1.1.1.1 query failed: {e}\n", "muted")

        threading.Thread(target=work, daemon=True).start()

    def benchmark(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        name = self.name.get().strip() or "example.com"

        def work():
            for label, server in PUBLIC_RESOLVERS:
                try:
                    dt, records, rcode = dns_query(server, name, "A", timeout=3)
                    answer = records[0][1] if records else "no answer"
                    tag = "fast" if dt < 60 else ("slow" if dt < 200 else "fail")
                    self.app.post(self.tree.insert, "", "end",
                                  values=(label, server, f"{dt:.0f} ms", answer),
                                  tags=(tag,))
                except Exception:
                    self.app.post(self.tree.insert, "", "end",
                                  values=(label, server, "—", "timeout"),
                                  tags=("fail",))

            # system resolver last
            try:
                t0 = time.time()
                socket.getaddrinfo(name, None)
                dt = (time.time() - t0) * 1000
                tag = "fast" if dt < 60 else "slow"
                self.app.post(self.tree.insert, "", "end",
                              values=("System", "(OS)", f"{dt:.0f} ms", "ok"),
                              tags=(tag,))
            except Exception:
                self.app.post(self.tree.insert, "", "end",
                              values=("System", "(OS)", "—", "failed"),
                              tags=("fail",))

        threading.Thread(target=work, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
#  Port scanner
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_NAMES = {
    20: "ftp-data", 21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp",
    53: "dns", 67: "dhcp", 68: "dhcp", 80: "http", 110: "pop3",
    111: "rpcbind", 123: "ntp", 135: "msrpc", 137: "netbios", 139: "netbios",
    143: "imap", 161: "snmp", 389: "ldap", 443: "https", 445: "smb",
    465: "smtps", 514: "syslog", 587: "smtp", 631: "ipp", 993: "imaps",
    995: "pop3s", 1080: "socks", 1433: "mssql", 1521: "oracle",
    1723: "pptp", 2049: "nfs", 3000: "dev-http", 3306: "mysql",
    3389: "rdp", 5000: "dev-http", 5432: "postgres", 5900: "vnc",
    6379: "redis", 8000: "http-alt", 8080: "http-alt", 8443: "https-alt",
    8888: "http-alt", 9000: "http-alt", 27017: "mongodb",
}

PRESETS = {
    "Common services": [20, 21, 22, 23, 25, 53, 80, 110, 111, 123, 135, 139,
                        143, 161, 389, 443, 445, 465, 587, 631, 993, 995,
                        1080, 1433, 1521, 2049, 3306, 3389, 5432, 5900, 6379,
                        8000, 8080, 8443, 27017],
    "Web only": [80, 443, 3000, 5000, 8000, 8008, 8080, 8081, 8443, 8888, 9000],
    "Windows / AD": [53, 88, 135, 137, 138, 139, 389, 445, 464, 636, 3268, 3389, 5985, 5986],
    "Databases": [1433, 1521, 3306, 5432, 6379, 9042, 11211, 27017],
}


class PortScanPage(Page):
    title = "Port Scanner"
    subtitle = "Multithreaded TCP connect scan with service identification"

    def build(self):
        self.host = tk.StringVar(value="127.0.0.1")
        self.preset = tk.StringVar(value="Common services")
        self.range_var = tk.StringVar(value="1-1024")
        self.scanning = False

        bar = tk.Frame(self.body, bg=C.BG)
        bar.pack(fill="x")

        tk.Label(bar, text="Host", bg=C.BG, fg=C.MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(0, 8))
        make_entry(bar, self.host, width=22).pack(side="left", ipady=6)

        tk.Label(bar, text="Preset", bg=C.BG, fg=C.MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(14, 8))
        cb = ttk.Combobox(bar, textvariable=self.preset, width=18,
                          values=list(PRESETS.keys()) + ["Custom range"],
                          state="readonly")
        cb.pack(side="left")

        tk.Label(bar, text="Range", bg=C.BG, fg=C.MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(14, 8))
        make_entry(bar, self.range_var, width=12).pack(side="left", ipady=6)

        self.btn = Btn(bar, "▶  Scan", self.start, "primary")
        self.btn.pack(side="left", padx=12)

        self.progress = ttk.Progressbar(self.body, mode="determinate")
        self.progress.pack(fill="x", pady=(14, 0))

        self.status = tk.Label(self.body, text="Ready.", bg=C.BG, fg=C.MUTED,
                               font=(FONT, 9))
        self.status.pack(anchor="w", pady=(6, 0))

        card = Card(self.body, "Results")
        card.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("port", "state", "service", "banner")
        self.tree = ttk.Treeview(card.inner, columns=cols, show="headings")
        for c, w in zip(cols, (90, 90, 150, 400)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("open", foreground=C.GREEN)

    def _ports(self):
        if self.preset.get() in PRESETS:
            return PRESETS[self.preset.get()]
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", self.range_var.get())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return list(range(min(a, b), max(a, b) + 1))
        m = re.match(r"^\s*(\d+)\s*$", self.range_var.get())
        if m:
            return [int(m.group(1))]
        return []

    def start(self):
        if self.scanning:
            return
        host = self.host.get().strip()
        ports = self._ports()
        if not host or not ports:
            messagebox.showwarning("NetForge", "Provide a host and a valid port range.")
            return
        if len(ports) > 65535:
            messagebox.showwarning("NetForge", "Too many ports.")
            return

        for row in self.tree.get_children():
            self.tree.delete(row)
        self.progress.configure(maximum=len(ports), value=0)
        self.scanning = True
        self.btn.set_text("Scanning…")
        self.status.configure(text=f"Scanning {host} — {len(ports)} ports…")
        threading.Thread(target=self._run, args=(host, ports), daemon=True).start()

    def _run(self, host, ports):
        t0 = time.time()
        done = 0
        open_count = 0
        lock = threading.Lock()

        def probe(port):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.8)
            try:
                if s.connect_ex((host, port)) == 0:
                    banner = ""
                    try:
                        s.settimeout(0.4)
                        s.sendall(b"\r\n")
                        banner = s.recv(128).decode("utf-8", "replace").strip()
                    except Exception:
                        pass
                    return port, banner
            except Exception:
                pass
            finally:
                s.close()
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=220) as pool:
            futures = {pool.submit(probe, p): p for p in ports}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                with lock:
                    done += 1
                    if res:
                        port, banner = res
                        open_count += 1
                        self.app.post(
                            self.tree.insert, "", "end",
                            values=(port, "open",
                                    SERVICE_NAMES.get(port, "unknown"),
                                    banner[:120]),
                            tags=("open",))
                    if done % 25 == 0 or done == len(ports):
                        self.app.post(self.progress.configure, {"value": done})
                        self.app.post(
                            self.status.configure,
                            {"text": f"{done}/{len(ports)} scanned · "
                                     f"{open_count} open"})

        elapsed = time.time() - t0
        self.app.post(self.status.configure,
                      {"text": f"Done — {len(ports)} ports in {elapsed:.1f}s · "
                               f"{open_count} open"})
        self.app.post(self.btn.set_text, "▶  Scan")
        self.scanning = False


# ─────────────────────────────────────────────────────────────────────────────
#  Speed test
# ─────────────────────────────────────────────────────────────────────────────

class SpeedTestPage(Page):
    title = "Speed Test"
    subtitle = "Throughput measurement against Cloudflare's public endpoints"

    DOWN_URL = "https://speed.cloudflare.com/__down?bytes={}"
    UP_URL = "https://speed.cloudflare.com/__up"

    def build(self):
        self.running = False
        self.down_var = tk.StringVar(value="—")
        self.up_var = tk.StringVar(value="—")
        self.lat_var = tk.StringVar(value="—")

        bar = tk.Frame(self.body, bg=C.BG)
        bar.pack(fill="x")
        self.size = tk.StringVar(value="25 MB")
        tk.Label(bar, text="Payload", bg=C.BG, fg=C.MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(0, 8))
        ttk.Combobox(bar, textvariable=self.size, width=10, state="readonly",
                     values=["5 MB", "10 MB", "25 MB", "50 MB"]).pack(side="left")
        self.btn = Btn(bar, "▶  Run test", self.start, "primary")
        self.btn.pack(side="left", padx=12)

        # metric tiles
        grid = tk.Frame(self.body, bg=C.BG)
        grid.pack(fill="x", pady=(18, 0))
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="m")

        for i, (cap, var, color) in enumerate([
                ("Download", self.down_var, C.ACCENT),
                ("Upload", self.up_var, C.GREEN),
                ("Idle latency", self.lat_var, C.AMBER)]):
            card = tk.Frame(grid, bg=C.PANEL, highlightbackground=C.BORDER,
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 6, 0))
            tk.Label(card, text=cap.upper(), bg=C.PANEL, fg=C.MUTED,
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=18,
                                                  pady=(16, 4))
            tk.Label(card, textvariable=var, bg=C.PANEL, fg=color,
                     font=(FONT, 22, "bold")).pack(anchor="w", padx=18,
                                                   pady=(0, 4))
            unit = tk.Label(card, text="Mbps" if i < 2 else "ms", bg=C.PANEL,
                            fg=C.DIM, font=(FONT, 9))
            unit.pack(anchor="w", padx=18, pady=(0, 16))

        card = Card(self.body, "Live throughput (Mbps)")
        card.pack(fill="both", expand=True, pady=(18, 0))
        self.graph = Sparkline(card.inner, height=160, color=C.GREEN,
                               max_points=200)
        self.graph.pack(fill="both", expand=True)

        self.status = tk.Label(self.body, text="Ready.", bg=C.BG, fg=C.MUTED,
                               font=(FONT, 9))
        self.status.pack(anchor="w", pady=(10, 0))

    def start(self):
        if self.running:
            return
        self.running = True
        self.btn.set_text("Running…")
        self.graph.clear()
        self.down_var.set("—")
        self.up_var.set("—")
        self.lat_var.set("—")
        mb = int(self.size.get().split()[0])
        threading.Thread(target=self._run, args=(mb,), daemon=True).start()

    def _run(self, mb):
        total_bytes = mb * 1024 * 1024

        # ── idle latency ────────────────────────────────────────────────────
        self.app.post(self.status.configure, {"text": "Measuring idle latency…"})
        samples = []
        for _ in range(4):
            rtt = ping_once("1.1.1.1", timeout=2)
            if rtt is not None:
                samples.append(rtt)
        if samples:
            self.app.post(self.lat_var.set, f"{min(samples):.0f}")

        # ── download ────────────────────────────────────────────────────────
        self.app.post(self.status.configure, {"text": "Downloading…"})
        try:
            url = self.DOWN_URL.format(total_bytes)
            req = urllib.request.Request(url, headers={"User-Agent": "NetForge/1.0"})
            t0 = time.time()
            received = 0
            last_t = t0
            last_b = 0
            with urllib.request.urlopen(req, timeout=30) as r:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    received += len(chunk)
                    now = time.time()
                    if now - last_t >= 0.25:
                        inst = (received - last_b) * 8 / (now - last_t) / 1e6
                        self.app.post(self.graph.push, inst)
                        self.app.post(
                            self.down_var.set,
                            f"{(received * 8 / (now - t0) / 1e6):.1f}")
                        last_t, last_b = now, received
            elapsed = time.time() - t0
            down_mbps = (received * 8 / elapsed / 1e6) if elapsed else 0
            self.app.post(self.down_var.set, f"{down_mbps:.1f}")
        except Exception as e:
            self.app.post(self.status.configure,
                          {"text": f"Download failed: {e}"})

        # ── upload ──────────────────────────────────────────────────────────
        self.app.post(self.status.configure, {"text": "Uploading…"})
        try:
            payload = b"\x00" * (mb * 1024 * 1024 // 4)
            req = urllib.request.Request(
                self.UP_URL, data=payload, method="POST",
                headers={"User-Agent": "NetForge/1.0",
                         "Content-Type": "application/octet-stream"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=40) as r:
                r.read()
            elapsed = time.time() - t0
            up_mbps = (len(payload) * 8 / elapsed / 1e6) if elapsed else 0
            self.app.post(self.up_var.set, f"{up_mbps:.1f}")
        except Exception as e:
            self.app.post(self.status.configure,
                          {"text": f"Upload failed: {e}"})

        self.app.post(self.status.configure,
                      {"text": f"Finished at {datetime.now().strftime('%H:%M:%S')}"})
        self.app.post(self.btn.set_text, "▶  Run test")
        self.running = False


# ─────────────────────────────────────────────────────────────────────────────
#  Bandwidth monitor
# ─────────────────────────────────────────────────────────────────────────────

class MonitorPage(Page):
    title = "Bandwidth Monitor"
    subtitle = "Live throughput across all network interfaces"

    def build(self):
        self.running = False
        self.down_var = tk.StringVar(value="0.0")
        self.up_var = tk.StringVar(value="0.0")
        self.tot_down = 0.0
        self.tot_up = 0.0

        bar = tk.Frame(self.body, bg=C.BG)
        bar.pack(fill="x")
        self.btn = Btn(bar, "▶  Start monitoring", self.toggle, "success")
        self.btn.pack(side="left")

        self.backend = tk.Label(bar, text="", bg=C.BG, fg=C.MUTED,
                                font=(FONT, 9))
        self.backend.pack(side="left", padx=14)

        grid = tk.Frame(self.body, bg=C.BG)
        grid.pack(fill="x", pady=(18, 0))
        grid.columnconfigure(0, weight=1, uniform="m")
        grid.columnconfigure(1, weight=1, uniform="m")
        for i, (cap, var, color) in enumerate([
                ("Download", self.down_var, C.ACCENT),
                ("Upload", self.up_var, C.PURPLE)]):
            card = tk.Frame(grid, bg=C.PANEL, highlightbackground=C.BORDER,
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 6, 0))
            tk.Label(card, text=cap.upper(), bg=C.PANEL, fg=C.MUTED,
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=18,
                                                  pady=(16, 2))
            row = tk.Frame(card, bg=C.PANEL)
            row.pack(anchor="w", padx=18, pady=(0, 16))
            tk.Label(row, textvariable=var, bg=C.PANEL, fg=color,
                     font=(FONT, 22, "bold")).pack(side="left")
            tk.Label(row, text=" Mbps", bg=C.PANEL, fg=C.DIM,
                     font=(FONT, 10)).pack(side="left", pady=(8, 0))

        card = Card(self.body, "Throughput history (Mbps)")
        card.pack(fill="both", expand=True, pady=(18, 0))
        self.graph = Sparkline(card.inner, height=200, max_points=180)
        self.graph.pack(fill="both", expand=True)

        self.totals = tk.Label(self.body, text="Session totals — ↓ 0 MB   ↑ 0 MB",
                               bg=C.BG, fg=C.MUTED, font=(MONO, 9))
        self.totals.pack(anchor="w", pady=(10, 0))

        self._detect_backend()

    def _detect_backend(self):
        try:
            import psutil  # noqa: F401
            self.backend.configure(text="Backend: psutil")
            return
        except Exception:
            pass
        if IS_LINUX and os.path.exists("/proc/net/dev"):
            self.backend.configure(text="Backend: /proc/net/dev")
        else:
            self.backend.configure(
                text="Backend unavailable — install psutil for live rates",
                fg=C.AMBER)

    def on_show(self):
        pass

    def toggle(self):
        if self.running:
            self.running = False
            self.btn.set_text("▶  Start monitoring")
            self.btn.set_kind("success")
        else:
            self.running = True
            self.btn.set_text("■  Stop")
            self.btn.set_kind("danger")
            threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _read_counters():
        """Return (bytes_sent, bytes_recv) or None."""
        try:
            import psutil  # type: ignore
            io = psutil.net_io_counters()
            return io.bytes_sent, io.bytes_recv
        except Exception:
            pass
        try:
            if IS_LINUX:
                sent = recv = 0
                with open("/proc/net/dev") as f:
                    for line in f.readlines()[2:]:
                        if ":" not in line:
                            continue
                        _, data = line.split(":", 1)
                        parts = data.split()
                        recv += int(parts[0])
                        sent += int(parts[8])
                return sent, recv
        except Exception:
            pass
        return None

    def _loop(self):
        prev = self._read_counters()
        if prev is None:
            self.app.post(self.btn.set_text, "▶  Start monitoring")
            self.app.post(self.btn.set_kind, "success")
            self.running = False
            return
        prev_t = time.time()
        while self.running:
            time.sleep(1.0)
            cur = self._read_counters()
            if cur is None:
                continue
            now = time.time()
            dt = now - prev_t
            down = (cur[1] - prev[1]) * 8 / dt / 1e6
            up = (cur[0] - prev[0]) * 8 / dt / 1e6
            self.tot_down += (cur[1] - prev[1])
            self.tot_up += (cur[0] - prev[0])
            prev, prev_t = cur, now

            self.app.post(self.down_var.set, f"{max(down,0):.2f}")
            self.app.post(self.up_var.set, f"{max(up,0):.2f}")
            self.app.post(self.graph.push, max(down, 0) + max(up, 0))
            self.app.post(self.totals.configure, {
                "text": f"Session totals — ↓ {self.tot_down/1e6:.1f} MB   "
                        f"↑ {self.tot_up/1e6:.1f} MB"})


# ─────────────────────────────────────────────────────────────────────────────
#  Tools: subnet calculator, Wake-on-LAN, traceroute
# ─────────────────────────────────────────────────────────────────────────────

class ToolsPage(Page):
    title = "Network Tools"
    subtitle = "Subnet math, Wake-on-LAN and route tracing"

    def build(self):
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True)

        nb.add(self._subnet_tab(nb), text="  Subnet Calculator  ")
        nb.add(self._wol_tab(nb), text="  Wake-on-LAN  ")
        nb.add(self._trace_tab(nb), text="  Traceroute  ")

    # ── subnet ──────────────────────────────────────────────────────────────
    def _subnet_tab(self, parent):
        f = tk.Frame(parent, bg=C.PANEL)
        self.cidr = tk.StringVar(value="192.168.1.10/24")

        top = tk.Frame(f, bg=C.PANEL)
        top.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(top, text="IPv4 / CIDR", bg=C.PANEL, fg=C.MUTED,
                 font=(FONT, 9)).pack(anchor="w")
        row = tk.Frame(top, bg=C.PANEL)
        row.pack(fill="x", pady=(4, 0))
        e = make_entry(row, self.cidr, width=32, mono=True)
        e.pack(side="left", ipady=7)
        e.bind("<Return>", lambda ev: self._calc_subnet())
        Btn(row, "Calculate", self._calc_subnet, "primary").pack(side="left", padx=10)

        self.subnet_out = tk.Frame(f, bg=C.PANEL)
        self.subnet_out.pack(fill="both", expand=True, padx=20, pady=(10, 20))
        self._calc_subnet()
        return f

    def _calc_subnet(self):
        for w in self.subnet_out.winfo_children():
            w.destroy()
        raw = self.cidr.get().strip()
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except Exception as e:
            tk.Label(self.subnet_out, text=f"Invalid input: {e}", bg=C.PANEL,
                     fg=C.RED, font=(FONT, 10)).pack(anchor="w")
            return

        hosts = list(net.hosts()) if net.num_addresses > 2 else []
        first = str(hosts[0]) if hosts else "—"
        last = str(hosts[-1]) if hosts else "—"

        rows = [
            ("Network address", str(net.network_address)),
            ("Broadcast address", str(net.broadcast_address) if net.version == 4 else "—"),
            ("Subnet mask", str(net.netmask)),
            ("Wildcard mask", str(net.hostmask)),
            ("Prefix length", f"/{net.prefixlen}"),
            ("Address class", self._class_of(net)),
            ("Total addresses", f"{net.num_addresses:,}"),
            ("Usable hosts", f"{max(net.num_addresses - 2, 0):,}"),
            ("First usable", first),
            ("Last usable", last),
            ("Is private", "Yes" if net.is_private else "No"),
            ("Reverse DNS zone", self._reverse_zone(net)),
        ]
        for k, v in rows:
            kv_row(self.subnet_out, k, v)

    @staticmethod
    def _class_of(net):
        if net.version != 4:
            return "IPv6"
        first = int(str(net.network_address).split(".")[0])
        if first < 128:
            return "A"
        if first < 192:
            return "B"
        if first < 224:
            return "C"
        if first < 240:
            return "D (multicast)"
        return "E (reserved)"

    @staticmethod
    def _reverse_zone(net):
        if net.version != 4 or net.prefixlen % 8:
            return "—"
        octets = str(net.network_address).split(".")[:net.prefixlen // 8]
        return ".".join(reversed(octets)) + ".in-addr.arpa"

    # ── wake on lan ─────────────────────────────────────────────────────────
    def _wol_tab(self, parent):
        f = tk.Frame(parent, bg=C.PANEL)
        self.wol_mac = tk.StringVar(value="AA:BB:CC:DD:EE:FF")
        self.wol_bcast = tk.StringVar(value="255.255.255.255")
        self.wol_port = tk.StringVar(value="9")

        inner = tk.Frame(f, bg=C.PANEL)
        inner.pack(fill="x", padx=20, pady=20)

        field(inner, "Target MAC address", self.wol_mac, width=32, mono=True)
        field(inner, "Broadcast address", self.wol_bcast, width=32, mono=True)
        field(inner, "Port (9 or 7)", self.wol_port, width=12, mono=True)

        Btn(inner, "⚡  Send magic packet", self._send_wol, "primary").pack(anchor="w")

        self.wol_status = tk.Label(inner, text="", bg=C.PANEL, fg=C.MUTED,
                                   font=(FONT, 9), justify="left")
        self.wol_status.pack(anchor="w", pady=(14, 0))

        hint = ("Note: the target machine must have Wake-on-LAN enabled in its "
                "BIOS/UEFI and NIC settings, and you must be on the same "
                "broadcast domain (or use a directed broadcast).")
        tk.Label(inner, text=hint, bg=C.PANEL, fg=C.DIM, font=(FONT, 9),
                 wraplength=620, justify="left").pack(anchor="w", pady=(16, 0))
        return f

    def _send_wol(self):
        mac = re.sub(r"[^0-9a-fA-F]", "", self.wol_mac.get())
        if len(mac) != 12:
            self.wol_status.configure(text="✗ Invalid MAC address.", fg=C.RED)
            return
        try:
            port = int(self.wol_port.get())
            payload = bytes.fromhex("FF" * 6 + mac * 16)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(payload, (self.wol_bcast.get().strip(), port))
            s.close()
            self.wol_status.configure(
                text=f"✓ Magic packet sent to {self.wol_mac.get()} "
                     f"via {self.wol_bcast.get()}:{port}",
                fg=C.GREEN)
        except Exception as e:
            self.wol_status.configure(text=f"✗ Failed: {e}", fg=C.RED)

    # ── traceroute ──────────────────────────────────────────────────────────
    def _trace_tab(self, parent):
        f = tk.Frame(parent, bg=C.PANEL)
        self.trace_host = tk.StringVar(value="1.1.1.1")

        top = tk.Frame(f, bg=C.PANEL)
        top.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(top, text="Target host", bg=C.PANEL, fg=C.MUTED,
                 font=(FONT, 9)).pack(anchor="w")
        row = tk.Frame(top, bg=C.PANEL)
        row.pack(fill="x", pady=(4, 0))
        e = make_entry(row, self.trace_host, width=32, mono=True)
        e.pack(side="left", ipady=7)
        e.bind("<Return>", lambda ev: self._trace())
        self.trace_btn = Btn(row, "▶  Trace", self._trace, "primary")
        self.trace_btn.pack(side="left", padx=10)

        self.trace_out = tk.Text(f, bg=C.PANEL_2, fg=C.TEXT, relief="flat",
                                 bd=0, font=(MONO, 9), highlightthickness=0,
                                 wrap="none")
        self.trace_out.pack(fill="both", expand=True, padx=20, pady=(10, 20))
        self.trace_out.tag_configure("ok", foreground=C.GREEN)
        self.trace_out.tag_configure("head", foreground=C.ACCENT)
        self.trace_out.configure(state="disabled")
        return f

    def _trace(self):
        host = self.trace_host.get().strip()
        if not host:
            return
        self.trace_out.configure(state="normal")
        self.trace_out.delete("1.0", "end")
        self.trace_out.insert("end", f"Tracing route to {host}…\n\n", "head")
        self.trace_out.configure(state="disabled")
        self.trace_btn.set_text("Tracing…")

        def work():
            if IS_WIN:
                cmd = ["tracert", "-d", "-h", "20", "-w", "1000", host]
            elif IS_MAC:
                cmd = ["traceroute", "-n", "-m", "20", "-w", "1", host]
            else:
                cmd = ["traceroute", "-n", "-m", "20", "-w", "1", host]
                if not have("traceroute"):
                    cmd = ["tracepath", "-m", "20", host]
            out = run(cmd, timeout=90)
            if not out.strip():
                out = "(no output — traceroute may not be installed or requires privileges)"
            for line in out.splitlines():
                tag = "ok" if re.search(r"\d+\.\d+\.\d+\.\d+", line) else None
                self.app.post(self._trace_append, line + "\n", tag)
            self.app.post(self.trace_btn.set_text, "▶  Trace")

        threading.Thread(target=work, daemon=True).start()

    def _trace_append(self, text, tag):
        self.trace_out.configure(state="normal")
        self.trace_out.insert("end", text, tag or ())
        self.trace_out.see("end")
        self.trace_out.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────────
#  Navigation item
# ─────────────────────────────────────────────────────────────────────────────

class NavItem(tk.Frame):
    def __init__(self, parent, icon, label, command):
        super().__init__(parent, bg=C.BG, cursor="hand2")
        self.active = False
        self.bar = tk.Frame(self, bg=C.BG, width=3)
        self.bar.pack(side="left", fill="y")
        self.icon = tk.Label(self, text=icon, bg=C.BG, fg=C.DIM,
                             font=(FONT, 12), width=3)
        self.icon.pack(side="left", padx=(8, 0), pady=9)
        self.label = tk.Label(self, text=label, bg=C.BG, fg=C.TEXT,
                              font=(FONT, 10), anchor="w")
        self.label.pack(side="left", fill="x", expand=True, pady=9)
        self.command = command
        for w in (self, self.icon, self.label):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _paint(self, bg):
        for w in (self, self.icon, self.label):
            w.configure(bg=bg)

    def _enter(self, _=None):
        if not self.active:
            self._paint(C.PANEL_2)

    def _leave(self, _=None):
        if not self.active:
            self._paint(C.BG)

    def set_active(self, on):
        self.active = on
        self._paint(C.PANEL if on else C.BG)
        self.bar.configure(bg=C.ACCENT if on else C.BG)
        self.label.configure(fg=C.ACCENT if on else C.TEXT)
        self.icon.configure(fg=C.ACCENT if on else C.DIM)


# ─────────────────────────────────────────────────────────────────────────────
#  Application shell
# ─────────────────────────────────────────────────────────────────────────────

PAGES = {
    "dashboard": (DashboardPage, "◈", "Dashboard"),
    "proxy":     (ProxyPage,     "⇄", "Proxy"),
    "latency":   (LatencyPage,   "◉", "Latency"),
    "dns":       (DNSLookupPage, "⌘", "DNS"),
    "ports":     (PortScanPage,  "▤", "Port Scanner"),
    "speed":     (SpeedTestPage, "⇅", "Speed Test"),
    "monitor":   (MonitorPage,   "◧", "Bandwidth"),
    "tools":     (ToolsPage,     "⚙", "Tools"),
}


class NetForge(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NetForge — Network Management Toolkit")
        self.geometry("1220x790")
        self.minsize(1040, 660)
        self.configure(bg=C.BG)

        self._queue: queue.Queue = queue.Queue()
        self.pages: dict[str, Page] = {}
        self.nav_items: dict[str, NavItem] = {}
        self.current = None

        self._style()
        self._layout()
        self.show("dashboard")
        self.after(40, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── thread-safe UI dispatch ─────────────────────────────────────────────
    def post(self, fn, *args, **kwargs):
        self._queue.put((fn, args, kwargs))

    def _drain(self):
        try:
            while True:
                fn, args, kwargs = self._queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    print("UI callback error:", e, file=sys.stderr)
        except queue.Empty:
            pass
        self.after(35, self._drain)

    # ── styling ─────────────────────────────────────────────────────────────
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass

        st.configure(".", background=C.BG, foreground=C.TEXT,
                     fieldbackground=C.PANEL_2, bordercolor=C.BORDER,
                     lightcolor=C.PANEL_2, darkcolor=C.PANEL_2,
                     focuscolor=C.ACCENT, troughcolor=C.PANEL_2,
                     font=(FONT, 10))

        st.configure("Treeview", background=C.PANEL, fieldbackground=C.PANEL,
                     foreground=C.TEXT, borderwidth=0, rowheight=26)
        st.configure("Treeview.Heading", background=C.PANEL_2,
                     foreground=C.MUTED, relief="flat",
                     font=(FONT, 9, "bold"), padding=(6, 6))
        st.map("Treeview",
               background=[("selected", C.ACCENT)],
               foreground=[("selected", "#ffffff")])
        st.map("Treeview.Heading", background=[("active", C.PANEL_3)])

        st.configure("TCombobox", fieldbackground=C.PANEL_2,
                     background=C.PANEL_2, foreground=C.TEXT,
                     arrowcolor=C.MUTED, bordercolor=C.BORDER,
                     selectbackground=C.PANEL_2, selectforeground=C.TEXT,
                     padding=(8, 6))
        st.map("TCombobox",
               fieldbackground=[("readonly", C.PANEL_2)],
               foreground=[("readonly", C.TEXT)])

        st.configure("TNotebook", background=C.BG, borderwidth=0,
                     tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=C.BG, foreground=C.MUTED,
                     padding=(18, 9), borderwidth=0,
                     font=(FONT, 10, "bold"))
        st.map("TNotebook.Tab",
               background=[("selected", C.PANEL)],
               foreground=[("selected", C.ACCENT)])

        st.configure("Horizontal.TProgressbar", background=C.ACCENT,
                     troughcolor=C.PANEL_2, bordercolor=C.PANEL_2,
                     lightcolor=C.ACCENT, darkcolor=C.ACCENT, thickness=6)

        st.configure("Vertical.TScrollbar", background=C.PANEL_2,
                     troughcolor=C.BG, bordercolor=C.BG, arrowcolor=C.MUTED)

        self.option_add("*TCombobox*Listbox.background", C.PANEL_2)
        self.option_add("*TCombobox*Listbox.foreground", C.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", C.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", (FONT, 10))

    # ── layout ──────────────────────────────────────────────────────────────
    def _layout(self):
        root = tk.Frame(self, bg=C.BG)
        root.pack(fill="both", expand=True)

        # Sidebar
        side = tk.Frame(root, bg=C.BG, width=226,
                        highlightbackground=C.BORDER, highlightthickness=0)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=C.BG)
        brand.pack(fill="x", pady=(22, 18))
        tk.Label(brand, text="⬢", bg=C.BG, fg=C.ACCENT,
                 font=(FONT, 20)).pack(side="left", padx=(16, 8))
        name = tk.Frame(brand, bg=C.BG)
        name.pack(side="left")
        tk.Label(name, text="NetForge", bg=C.BG, fg=C.TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(name, text="network toolkit", bg=C.BG, fg=C.DIM,
                 font=(FONT, 8)).pack(anchor="w")

        tk.Frame(side, bg=C.BORDER, height=1).pack(fill="x", padx=14)

        for key, (_, icon, label) in PAGES.items():
            item = NavItem(side, icon, label, lambda k=key: self.show(k))
            item.pack(fill="x", pady=1)
            self.nav_items[key] = item

        tk.Frame(side, bg=C.BORDER, height=1).pack(fill="x", padx=14,
                                                   side="bottom", pady=(0, 8))
        self.side_status = tk.Label(side, text="Ready", bg=C.BG, fg=C.DIM,
                                    font=(FONT, 8), wraplength=196,
                                    justify="left", anchor="w")
        self.side_status.pack(side="bottom", fill="x", padx=16, pady=(0, 12))

        # Main region
        main = tk.Frame(root, bg=C.BG)
        main.pack(side="left", fill="both", expand=True)

        tk.Frame(main, bg=C.BORDER, width=1).pack(side="left", fill="y")

        self.container = tk.Frame(main, bg=C.BG)
        self.container.pack(fill="both", expand=True)

    # ── page routing ────────────────────────────────────────────────────────
    def show(self, key: str):
        if key not in PAGES:
            return
        if key not in self.pages:
            cls = PAGES[key][0]
            self.pages[key] = cls(self.container, self)

        for p in self.pages.values():
            p.pack_forget()
        page = self.pages[key]
        page.pack(fill="both", expand=True)
        self.current = key

        for k, item in self.nav_items.items():
            item.set_active(k == key)

        try:
            page.on_show()
        except Exception as e:
            print("on_show error:", e, file=sys.stderr)

    def set_status(self, text: str):
        self.side_status.configure(
            text=f"{text[:70]}\n{datetime.now().strftime('%H:%M:%S')}")

    def _on_close(self):
        # Stop background loops gracefully
        for page in self.pages.values():
            if isinstance(page, (LatencyPage, MonitorPage)):
                page.running = False
                if hasattr(page, "_stop"):
                    page._stop.set()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    try:
        app = NetForge()
    except tk.TclError as e:
        print("Could not start GUI:", e, file=sys.stderr)
        print("Ensure a display is available (or install python3-tk).",
              file=sys.stderr)
        sys.exit(1)
    app.mainloop()


if __name__ == "__main__":
    main()