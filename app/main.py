"""Router-Stats: Web-API + Dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .collector import Collector
from .fritz import FritzBox
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC = Path(__file__).parent / "static"
store = Storage(config.DB_PATH)
fb = FritzBox(config.FRITZ_HOST, config.FRITZ_USER, config.FRITZ_PASSWORD)
collector = Collector(fb, store)

SERIES = {
    "wan_rx_mbps", "wan_tx_mbps", "ping_router_avg", "ping_router_max", "ping_router_loss",
    "ping_inet_avg", "ping_inet_max", "ping_inet_loss", "dsl_snr_down", "dsl_snr_up",
    "dsl_sync_down", "dsl_sync_up", "dsl_crc_delta", "wlan_clients_24", "wlan_clients_5", "devices_total",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    collector.start()
    yield
    collector.stop()


app = FastAPI(title="Router-Stats", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/overview")
def overview():
    s = store.all_state()
    return {
        "state": {k: v["data"] for k, v in s.items()},
        "updated": {k: v["ts"] for k, v in s.items()},
        "errors": collector.errors,
        "speedtest_running": collector.speedtest_running,
        "config": {
            "host": config.FRITZ_HOST,
            "poll_interval": config.POLL_INTERVAL,
            "speedtest_interval_min": config.SPEEDTEST_INTERVAL_MIN,
            "ping_targets": config.PING_TARGETS,
            "has_password": bool(config.FRITZ_PASSWORD),
        },
    }


@app.get("/api/history")
def history(names: str = Query(...), hours: float = Query(24, gt=0, le=24 * 31)):
    wanted = [n for n in names.split(",") if n in SERIES]
    return {n: store.series(n, hours) for n in wanted}


@app.get("/api/speedtests")
def speedtests(days: float = Query(7, gt=0, le=31)):
    return store.speedtests(days)


@app.post("/api/speedtest")
def start_speedtest():
    started = collector.run_speedtest()
    return JSONResponse({"started": started}, status_code=202 if started else 409)


@app.post("/api/refresh")
def refresh():
    collector.slow()
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = Query(50, gt=0, le=500)):
    return store.events(limit)


@app.get("/api/health")
def health():
    return {"ok": True, "errors": collector.errors}
