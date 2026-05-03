# stats_server.py
import os
import time
import json
import shutil
import socket
import subprocess

import psutil
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

try:
    import docker
except ImportError:
    docker = None

try:
    import requests
except ImportError:
    requests = None

# ---- CONFIG VIA ENV OR DIRECT EDIT ----
HOST_NAME   = os.getenv("HOST_NAME", socket.gethostname())
ROLE        = os.getenv("HOST_ROLE", "docker")  # "docker" or "ai"
LISTEN_PORT = int(os.getenv("STATS_PORT", "8000"))

# Disk mount points to report (comma-separated)
# Comma-separated mount points to report.
# Default covers bossbitch's two physical mounts — override via env var
# for other boxes (e.g. DISK_MOUNTS="/,/var/lib/ollama" on the AI box).
DISK_MOUNTS = os.getenv("DISK_MOUNTS", "/,/mnt/sata").split(",")

# Ollama (for AI box)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# Max processes to return in detail view
DETAIL_PROC_LIMIT = int(os.getenv("DETAIL_PROC_LIMIT", "12"))

# ---------------------------------------

app = FastAPI(title=f"{HOST_NAME} stats API")

# Net I/O snapshot for delta calculation (module-level, updated each detail call)
_net_snap = psutil.net_io_counters()
_net_ts   = time.time()


# ============================================================
# Existing helpers — unchanged
# ============================================================

def get_ip_addresses():
    ips = []
    for iface, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family.name == "AF_INET" and not a.address.startswith("127."):
                ips.append(a.address)
    return ips


def get_disks():
    disks = []
    for m in DISK_MOUNTS:
        m = m.strip()
        if not m:
            continue
        try:
            usage = shutil.disk_usage(m)
            used_pct = usage.used / usage.total * 100.0
            disks.append({
                "mount":    m,
                "used_pct": round(used_pct, 1),
                "total_gb": round(usage.total / (1024**3), 1),
            })
        except FileNotFoundError:
            continue
    return disks


def get_docker_stats():
    if ROLE != "docker" or docker is None:
        return None

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
    except Exception as e:
        return {
            "error": f"docker_error: {e.__class__.__name__}",
            "running": 0,
            "total": 0,
            "unhealthy": 0,
            "unhealthy_names": [],
        }

    running = 0
    unhealthy = 0
    unhealthy_names = []

    for c in containers:
        status = c.status or ""
        if status == "running":
            running += 1
        try:
            inspect = c.attrs
            health  = inspect.get("State", {}).get("Health", {})
            if health and health.get("Status") != "healthy":
                unhealthy += 1
                unhealthy_names.append(c.name)
        except Exception:
            pass

    return {
        "running":         running,
        "total":           len(containers),
        "unhealthy":       unhealthy,
        "unhealthy_names": unhealthy_names,
    }


def get_ollama_stats():
    if ROLE != "ai" or requests is None:
        return None

    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=1.5)
        if r.status_code != 200:
            return {"status": "down", "error": f"http_{r.status_code}"}
        data   = r.json()
        models = data.get("models", []) or []
        names  = [m.get("name", "unknown") for m in models]
        return {
            "status":         "up",
            "running_models": len(models),
            "current_models": names,
        }
    except Exception as e:
        return {"status": "down", "error": f"{e.__class__.__name__}"}


# ============================================================
# New helpers — screen 3 detail fields
# ============================================================

def get_cpu_temps():
    """
    Returns a dict of {core_index: temp_celsius}.
    Tries common Linux sensor keys in order; returns {} if unavailable.
    """
    try:
        raw = psutil.sensors_temperatures()
    except AttributeError:
        return {}  # Windows / unsupported platform

    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        if key not in raw:
            continue
        out = {}
        idx = 0
        for entry in raw[key]:
            # coretemp labels are "Core 0", "Core 1" etc; k10temp uses "Tctl"/"Tccd*"
            if "Core" in entry.label or "Tctl" in entry.label or "Tccd" in entry.label:
                out[idx] = round(entry.current, 1)
                idx += 1
        if out:
            return out

    return {}


def get_net_delta():
    """
    Returns (up_kbs, dn_kbs, pkt_sent_total, pkt_recv_total) since last call.
    Updates the module-level snapshot in place.
    """
    global _net_snap, _net_ts

    now_snap = psutil.net_io_counters()
    now_ts   = time.time()
    dt       = max(now_ts - _net_ts, 0.001)

    up_kbs = round((now_snap.bytes_sent - _net_snap.bytes_sent) / dt / 1024, 2)
    dn_kbs = round((now_snap.bytes_recv - _net_snap.bytes_recv) / dt / 1024, 2)

    _net_snap = now_snap
    _net_ts   = now_ts

    return up_kbs, dn_kbs, now_snap.packets_sent, now_snap.packets_recv


def get_detail_stats():
    """
    Extra payload for screen 3 (btop view).
    Returned as a nested 'detail' key so existing consumers see no change.
    """
    # Per-core CPU — call with percpu=True; brief interval for accuracy
    cores_pct  = psutil.cpu_percent(interval=0.1, percpu=True)
    cores_freq = psutil.cpu_freq(percpu=True) or []
    temps      = get_cpu_temps()

    cores = []
    for i, pct in enumerate(cores_pct):
        cores.append({
            "pct":  round(pct, 1),
            "mhz":  round(cores_freq[i].current) if i < len(cores_freq) else 0,
            "temp": temps.get(i),  # None if unavailable
        })

    # Memory detail
    mem  = psutil.virtual_memory()
    swap = psutil.swap_memory()

    mem_detail = {
        "total_gb":  round(mem.total     / (1024**3), 1),
        "used_gb":   round(mem.used      / (1024**3), 2),
        "avail_gb":  round(mem.available / (1024**3), 1),
        "cached_gb": round(getattr(mem, "cached", 0) / (1024**3), 1),
        "free_gb":   round(mem.free      / (1024**3), 1),
        "percent":   round(mem.percent,  1),
    }

    swap_detail = {
        "total_gb": round(swap.total / (1024**3), 1),
        "used_gb":  round(swap.used  / (1024**3), 2),
        "percent":  round(swap.percent, 1),
    }

    # Network delta
    up_kbs, dn_kbs, pkt_sent, pkt_recv = get_net_delta()
    net = {
        "up_kbs":   up_kbs,
        "dn_kbs":   dn_kbs,
        "pkt_sent": pkt_sent,
        "pkt_recv": pkt_recv,
    }

    # Top processes sorted by CPU%, capped at DETAIL_PROC_LIMIT
    procs = []
    try:
        proc_list = sorted(
            psutil.process_iter([
                "pid", "name", "cmdline", "cpu_percent",
                "memory_percent", "num_threads",
                "memory_info", "status", "username",
            ]),
            key=lambda p: (
                -(p.info.get("cpu_percent") or 0),
                -(p.info.get("memory_info").rss if p.info.get("memory_info") else 0),
            ),
        )[:DETAIL_PROC_LIMIT]

        for p in proc_list:
            i = p.info
            cmdline = i.get("cmdline") or []
            procs.append({
                "pid":    i["pid"],
                "name":   i["name"],
                "cmd":    " ".join(cmdline[:3]) if cmdline else i["name"],
                "cpu":    round(i.get("cpu_percent") or 0, 1),
                "mem":    round(i.get("memory_percent") or 0, 1),
                "thr":    i.get("num_threads", 0),
                "mem_mb": round((i["memory_info"].rss if i.get("memory_info") else 0) / (1024**2), 1),
                "status": i.get("status", ""),
                "user":   i.get("username", ""),
            })
    except Exception:
        pass  # return whatever we have so far

    # Load average + uptime
    load1, load5, load15 = psutil.getloadavg()
    uptime_s = round(time.time() - psutil.boot_time())

    return {
        "cores":      cores,
        "mem_detail": mem_detail,
        "swap":       swap_detail,
        "net":        net,
        "procs":      procs,
        "load":       [round(load1, 2), round(load5, 2), round(load15, 2)],
        "uptime_s":   uptime_s,
    }


# ============================================================
# Route — detail=full adds the screen 3 block, nothing else changes
# ============================================================

@app.get("/stats")
def stats(detail: bool = Query(default=False)):
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    disks = get_disks()
    ips   = get_ip_addresses()

    resp = {
        "host": HOST_NAME,
        "role": ROLE,
        "ip":   ips[0] if ips else None,
        "ip_all": ips,
        "cpu":  cpu,
        "mem": {
            "percent":  mem.percent,
            "used_gb":  round(mem.used  / (1024**3), 1),
            "total_gb": round(mem.total / (1024**3), 1),
        },
        "disks": disks,
        "ts":    time.time(),
    }

    if ROLE == "docker":
        resp["docker"] = get_docker_stats()
    if ROLE == "ai":
        resp["ollama"] = get_ollama_stats()

    # Screen 3 detail block — only populated when explicitly requested
    if detail:
        resp["detail"] = get_detail_stats()

    return JSONResponse(resp)


# For uvicorn entrypoint:
#   uvicorn stats_server:app --host 0.0.0.0 --port 8000
