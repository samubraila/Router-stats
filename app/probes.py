"""Messungen aus Sicht des Docker-Hosts: Ping (Latenz/Jitter/Verlust) und Speedtest."""
from __future__ import annotations

import re
import subprocess
import time

import httpx

CF = "https://speed.cloudflare.com"


def ping(target: str, count: int = 10, interval: float = 0.2) -> dict | None:
    try:
        p = subprocess.run(
            ["ping", "-n", "-q", "-c", str(count), "-i", str(interval), "-W", "1", target],
            capture_output=True, text=True, timeout=count * 2 + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    res = {"target": target, "loss_pct": float(loss.group(1)) if loss else 100.0}
    if rtt:
        res.update(min_ms=float(rtt.group(1)), avg_ms=float(rtt.group(2)),
                   max_ms=float(rtt.group(3)), jitter_ms=float(rtt.group(4)))
    return res


def _latency(client: httpx.Client, n: int = 5) -> float | None:
    vals = []
    for _ in range(n):
        t = time.perf_counter()
        try:
            client.get(f"{CF}/__down?bytes=0")
        except httpx.HTTPError:
            continue
        vals.append((time.perf_counter() - t) * 1000)
    return round(sorted(vals)[len(vals) // 2], 1) if vals else None


def speedtest(down_mb: int, up_mb: int) -> dict:
    """Download/Upload gegen Cloudflare. Liefert Mbit/s."""
    res: dict = {"down_mbps": None, "up_mbps": None, "latency_ms": None, "error": None}
    with httpx.Client(timeout=60, headers={"User-Agent": "router-stats"}) as c:
        try:
            res["latency_ms"] = _latency(c)
            size = down_mb * 1_000_000
            t0 = time.perf_counter()
            got = 0
            with c.stream("GET", f"{CF}/__down?bytes={size}") as r:
                r.raise_for_status()
                for chunk in r.iter_bytes(65536):
                    got += len(chunk)
            dt = time.perf_counter() - t0
            res["down_mbps"] = round(got * 8 / dt / 1e6, 1)

            payload = b"\0" * (up_mb * 1_000_000)
            t0 = time.perf_counter()
            r = c.post(f"{CF}/__up", content=payload)
            r.raise_for_status()
            dt = time.perf_counter() - t0
            res["up_mbps"] = round(len(payload) * 8 / dt / 1e6, 1)
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)
    return res
