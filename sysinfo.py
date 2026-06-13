#!/usr/bin/env python3
"""Read-only host resource stats for the VPS-stats skill.

Reads /proc and statvfs directly in Python (no shell, no root): the
tg-ingest service user can read all of this. Pure functions parsing
/proc text are unit-tested; collect() is the live wrapper.
"""
import os


def parse_meminfo(text):
    """/proc/meminfo -> (total_bytes, available_bytes)."""
    values = {}
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) == 2:
            num = parts[1].strip().split()
            if num and num[0].isdigit():
                values[parts[0]] = int(num[0]) * 1024  # kB -> bytes
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return total, available


def parse_loadavg(text):
    parts = text.split()
    return tuple(float(p) for p in parts[:3]) if len(parts) >= 3 else (0.0, 0.0, 0.0)


def parse_uptime(text):
    """/proc/uptime -> seconds (float)."""
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return 0.0


def parse_status_rss(text):
    """/proc/self/status -> resident set size in bytes (VmRSS)."""
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            num = line.split(":")[1].strip().split()
            if num and num[0].isdigit():
                return int(num[0]) * 1024
    return 0


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def fmt_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def collect(state_dir="/"):
    """Live snapshot; never raises — missing data degrades to zeros."""
    total_mem, avail_mem = parse_meminfo(_read("/proc/meminfo"))
    load1, load5, load15 = parse_loadavg(_read("/proc/loadavg"))
    uptime = parse_uptime(_read("/proc/uptime"))
    rss = parse_status_rss(_read("/proc/self/status"))
    try:
        st = os.statvfs(state_dir)
        disk_total = st.f_blocks * st.f_frsize
        disk_free = st.f_bavail * st.f_frsize
    except OSError:
        disk_total = disk_free = 0
    try:
        cpus = os.cpu_count() or 1
    except Exception:
        cpus = 1
    return {
        "cpus": cpus,
        "load": (load1, load5, load15),
        "mem_total": total_mem,
        "mem_used": total_mem - avail_mem,
        "mem_avail": avail_mem,
        "disk_total": disk_total,
        "disk_used": disk_total - disk_free,
        "disk_free": disk_free,
        "uptime_seconds": uptime,
        "agent_rss": rss,
    }


def format_report(data, lang, media_bytes=None):
    ru = lang == "ru"
    load = data["load"]
    cpus = data["cpus"] or 1
    load_pct = (load[0] / cpus) * 100
    mem_pct = (data["mem_used"] / data["mem_total"] * 100) if data["mem_total"] else 0
    disk_pct = (data["disk_used"] / data["disk_total"] * 100) if data["disk_total"] else 0
    lines = ["🖥 " + ("Состояние сервера:" if ru else "Server status:")]
    lines.append(
        ("Нагрузка CPU: " if ru else "CPU load: ")
        + f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} ({load_pct:.0f}% of {cpus} vCPU)"
    )
    lines.append(
        ("Память: " if ru else "Memory: ")
        + f"{fmt_bytes(data['mem_used'])} / {fmt_bytes(data['mem_total'])} ({mem_pct:.0f}%)"
    )
    lines.append(
        ("Диск: " if ru else "Disk: ")
        + f"{fmt_bytes(data['disk_used'])} / {fmt_bytes(data['disk_total'])} ({disk_pct:.0f}%),"
        + (" свободно " if ru else " free ") + fmt_bytes(data["disk_free"])
    )
    lines.append(("Аптайм: " if ru else "Uptime: ") + fmt_duration(data["uptime_seconds"]))
    footprint = ("Моя память: " if ru else "My footprint: ") + fmt_bytes(data["agent_rss"])
    if media_bytes is not None:
        footprint += (", медиа " if ru else ", media ") + fmt_bytes(media_bytes)
    lines.append(footprint)
    return "\n".join(lines)
