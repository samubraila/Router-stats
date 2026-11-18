"""SQLite-Speicher: Zeitreihen, Speedtests, Ereignisse und letzter Zustand."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (ts INTEGER NOT NULL, name TEXT NOT NULL, value REAL);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(name, ts);
CREATE TABLE IF NOT EXISTS speedtests (
    ts INTEGER PRIMARY KEY, down REAL, up REAL, latency REAL,
    sync_down REAL, sync_up REAL, background REAL, error TEXT);
CREATE TABLE IF NOT EXISTS events (ts INTEGER NOT NULL, level TEXT, area TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS ix_events ON events(ts);
CREATE TABLE IF NOT EXISTS state (kind TEXT PRIMARY KEY, ts INTEGER, data TEXT);
"""


class Storage:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    # --------------------------------------------------------------- write
    def add_metrics(self, values: dict[str, float | None], ts: int | None = None) -> None:
        ts = ts or int(time.time())
        rows = [(ts, k, float(v)) for k, v in values.items() if v is not None]
        if rows:
            with self.lock:
                self.db.executemany("INSERT INTO metrics VALUES (?,?,?)", rows)
                self.db.commit()

    def add_speedtest(self, r: dict) -> None:
        self._q("INSERT OR REPLACE INTO speedtests VALUES (?,?,?,?,?,?,?,?)",
                (r["ts"], r.get("down_mbps"), r.get("up_mbps"), r.get("latency_ms"),
                 r.get("sync_down"), r.get("sync_up"), r.get("background_mbps"), r.get("error")))

    def add_event(self, level: str, area: str, message: str) -> None:
        self._q("INSERT INTO events VALUES (?,?,?,?)", (int(time.time()), level, area, message))

    def set_state(self, kind: str, data) -> None:
        self._q("INSERT OR REPLACE INTO state VALUES (?,?,?)", (kind, int(time.time()), json.dumps(data)))

    def prune(self, days: int) -> None:
        cut = int(time.time()) - days * 86400
        for t in ("metrics", "speedtests", "events"):
            self._q(f"DELETE FROM {t} WHERE ts < ?", (cut,))

    # ---------------------------------------------------------------- read
    def get_state(self, kind: str):
        rows = self._q("SELECT ts, data FROM state WHERE kind=?", (kind,))
        return (rows[0][0], json.loads(rows[0][1])) if rows else (None, None)

    def all_state(self) -> dict:
        return {k: {"ts": ts, "data": json.loads(d)} for k, ts, d in self._q("SELECT kind, ts, data FROM state")}

    def series(self, name: str, hours: float, points: int = 300) -> list[list]:
        since = int(time.time() - hours * 3600)
        bucket = max(1, int(hours * 3600 / points))
        rows = self._q(
            "SELECT (ts/?)*? AS t, AVG(value), MAX(value) FROM metrics "
            "WHERE name=? AND ts>=? GROUP BY t ORDER BY t", (bucket, bucket, name, since))
        return [[t, round(a, 3), round(m, 3)] for t, a, m in rows]

    def stats(self, name: str, hours: float) -> dict | None:
        since = int(time.time() - hours * 3600)
        rows = self._q("SELECT AVG(value), MAX(value), MIN(value), COUNT(*) FROM metrics "
                       "WHERE name=? AND ts>=?", (name, since))
        a, mx, mn, n = rows[0]
        return None if not n else {"avg": a, "max": mx, "min": mn, "n": n}

    def count_above(self, name: str, hours: float, threshold: float) -> int:
        since = int(time.time() - hours * 3600)
        return self._q("SELECT COUNT(*) FROM metrics WHERE name=? AND ts>=? AND value>=?",
                       (name, since, threshold))[0][0]

    def speedtests(self, days: float) -> list[dict]:
        since = int(time.time() - days * 86400)
        cols = ["ts", "down", "up", "latency", "sync_down", "sync_up", "background", "error"]
        return [dict(zip(cols, r)) for r in
                self._q("SELECT * FROM speedtests WHERE ts>=? ORDER BY ts", (since,))]

    def events(self, limit: int = 50) -> list[dict]:
        return [{"ts": t, "level": l, "area": a, "message": m} for t, l, a, m in
                self._q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))]
