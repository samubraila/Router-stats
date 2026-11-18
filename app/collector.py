"""Hintergrund-Sammler: fragt Router und Messungen periodisch ab und speichert alles."""
from __future__ import annotations

import logging
import threading
import time

from . import analyzer, config, probes
from .analyzer import de
from .fritz import FritzBox
from .storage import Storage

log = logging.getLogger("collector")


def _mbit(bps: float | None) -> float | None:
    return None if bps is None else round(bps * 8 / 1e6, 3)


class Collector:
    def __init__(self, fb: FritzBox, store: Storage):
        self.fb = fb
        self.store = store
        self.errors: dict[str, str] = {}
        self.speedtest_running = False
        self._prev_dsl: dict | None = None
        self._prev_conn: dict | None = None
        last = store.speedtests(days=config.RETENTION_DAYS)
        self._last_speedtest = last[-1]["ts"] if last else 0
        self._stop = threading.Event()

    # ----------------------------------------------------------- helpers
    def _try(self, name: str, fn):
        try:
            v = fn()
            self.errors.pop(name, None)
            return v
        except Exception as e:  # noqa: BLE001
            self.errors[name] = f"{type(e).__name__}: {e}"
            log.warning("%s fehlgeschlagen: %s", name, e)
            return None

    # ------------------------------------------------------- fast cycle
    def fast(self) -> None:
        m: dict[str, float | None] = {}

        t = self._try("traffic", self.fb.traffic)
        if t:
            self.store.set_state("traffic", t)
            m["wan_rx_mbps"] = _mbit(t["rx_Bps"])
            m["wan_tx_mbps"] = _mbit(t["tx_Bps"])

        d = self._try("dsl", self.fb.dsl)
        if d:
            p = self._prev_dsl
            if p:
                crc = (d.get("crc_errors") or 0) - (p.get("crc_errors") or 0)
                m["dsl_crc_delta"] = max(0, crc)
                if (d.get("link_retrain") or 0) > (p.get("link_retrain") or 0):
                    self.store.add_event("crit", "DSL", "DSL-Resync: Die Leitung hat sich neu synchronisiert "
                                         f"(jetzt {de(d['sync_down_kbps']/1000)}/{de(d['sync_up_kbps']/1000)} Mbit/s)")
            self._prev_dsl = d
            self.store.set_state("dsl", d)
            m.update(dsl_snr_down=d["snr_down_db"], dsl_snr_up=d["snr_up_db"],
                     dsl_sync_down=(d["sync_down_kbps"] or 0) / 1000, dsl_sync_up=(d["sync_up_kbps"] or 0) / 1000)

        c = self._try("connection", self.fb.connection)
        if c:
            p = self._prev_conn
            if p and c.get("uptime_s") is not None and p.get("uptime_s") is not None \
                    and c["uptime_s"] < p["uptime_s"]:
                self.store.add_event("warn", "Internet", "Internetverbindung wurde neu aufgebaut "
                                     "(Zwangstrennung oder Abbruch)")
            if p and p.get("status") == "Connected" and c.get("status") != "Connected":
                self.store.add_event("crit", "Internet", f"Internetverbindung getrennt ({c.get('last_error')})")
            self._prev_conn = c
            self.store.set_state("connection", c)

        pings = {}
        r = probes.ping(config.FRITZ_HOST)
        if r:
            pings["router"] = r
            m.update(ping_router_avg=r.get("avg_ms"), ping_router_max=r.get("max_ms"),
                     ping_router_jitter=r.get("jitter_ms"), ping_router_loss=r["loss_pct"])
        inet = [x for x in (probes.ping(tg) for tg in config.PING_TARGETS) if x]
        if inet:
            pings["internet"] = inet
            ok = [x for x in inet if "avg_ms" in x]
            if ok:
                m.update(ping_inet_avg=sum(x["avg_ms"] for x in ok) / len(ok),
                         ping_inet_max=max(x["max_ms"] for x in ok),
                         ping_inet_jitter=sum(x["jitter_ms"] for x in ok) / len(ok))
            m["ping_inet_loss"] = sum(x["loss_pct"] for x in inet) / len(inet)
        if pings:
            self.store.set_state("ping", pings)
            self.errors.pop("ping", None)
        else:
            self.errors["ping"] = "Ping nicht möglich"

        self.store.add_metrics(m)
        self.analyze()

    # ------------------------------------------------------- slow cycle
    def slow(self) -> None:
        info = self._try("device_info", self.fb.device_info)
        if info:
            prev_ts, prev = self.store.get_state("device")
            if prev and info.get("uptime_s") and prev.get("uptime_s") and info["uptime_s"] < prev["uptime_s"]:
                self.store.add_event("warn", "Router", "FRITZ!Box wurde neu gestartet")
            self.store.set_state("device", info)

        link = self._try("link", self.fb.link)
        if link:
            self.store.set_state("link", link)

        radios = self._try("wlan", self.fb.wlan)
        if radios:
            self.store.set_state("wlan", radios)

        if config.FRITZ_PASSWORD:
            extras = self._try("wlan_extras", self.fb.wlan_extras)
            if extras:
                self.store.set_state("wlan_extras", extras)

        hosts = self._try("hosts", self.fb.hosts) or []
        wdev = self._try("wlan_devices", self.fb.wlan_devices) or []
        by_mac = {(w.get("mac") or "").upper(): w for w in wdev}
        by_ip = {w.get("ip"): w for w in wdev}
        devices = []
        for h in hosts:
            w = by_mac.get((h.get("mac") or "").upper()) or by_ip.get(h.get("ip"))
            is_wlan = bool(w) or "802.11" in str(h.get("interface"))
            devices.append({
                "name": h.get("name"), "ip": h.get("ip"),
                "connection": "WLAN" if is_wlan else (h.get("interface") or "LAN"),
                "band": w.get("band") if w else None,
                "signal_pct": w.get("signal_pct") if w else None,
                "speed_mbit": (w.get("speed_mbit") if w else None) or h.get("speed_mbit"),
                "guest": h.get("guest") or (w or {}).get("guest", False),
            })
        if hosts:
            devices.sort(key=lambda x: (x["connection"] != "WLAN", x["signal_pct"] if x["signal_pct"] is not None else 999))
            self.store.set_state("devices", devices)
            self.store.add_metrics({
                "wlan_clients_24": sum(1 for x in devices if x["band"] == "2,4 GHz"),
                "wlan_clients_5": sum(1 for x in devices if x["band"] == "5 GHz"),
                "devices_total": len(devices),
            })
        self.store.prune(config.RETENTION_DAYS)
        self.analyze()

    # ---------------------------------------------------------- speedtest
    def run_speedtest(self) -> bool:
        if self.speedtest_running:
            return False
        self.speedtest_running = True

        def job():
            try:
                bg = None
                t = self._try("traffic", self.fb.traffic)
                if t:
                    bg = _mbit(t["rx_Bps"])
                res = probes.speedtest(config.SPEEDTEST_DOWN_MB, config.SPEEDTEST_UP_MB)
                _, dsl = self.store.get_state("dsl")
                _, link = self.store.get_state("link")
                src = dsl if dsl and dsl.get("sync_down_kbps") else (
                    {"sync_down_kbps": link.get("max_down_kbps"), "sync_up_kbps": link.get("max_up_kbps")} if link else {})
                res.update(ts=int(time.time()), background_mbps=bg,
                           sync_down=(src.get("sync_down_kbps") or 0) / 1000 or None,
                           sync_up=(src.get("sync_up_kbps") or 0) / 1000 or None)
                self.store.add_speedtest(res)
                if res.get("error"):
                    self.store.add_event("warn", "Internet", f"Speedtest fehlgeschlagen: {res['error']}")
                self._last_speedtest = res["ts"]
                self.analyze()
            finally:
                self.speedtest_running = False

        threading.Thread(target=job, daemon=True, name="speedtest").start()
        return True

    # ------------------------------------------------------------ analyze
    def analyze(self) -> None:
        try:
            state = {k: v["data"] for k, v in self.store.all_state().items()}
            self.store.set_state("findings", analyzer.analyze(state, self.store))
        except Exception as e:  # noqa: BLE001
            log.exception("Analyse fehlgeschlagen")
            self.errors["analyze"] = str(e)

    # --------------------------------------------------------------- loop
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="collector").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        last_slow = 0.0
        started = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            if t0 - last_slow >= config.SLOW_INTERVAL:
                self.slow()
                last_slow = t0
            self.fast()
            iv = config.SPEEDTEST_INTERVAL_MIN * 60
            if iv and t0 - started > 60 and t0 - self._last_speedtest >= iv:
                self.run_speedtest()
            self._stop.wait(max(1.0, config.POLL_INTERVAL - (time.time() - t0)))
