"""Konfiguration aus Umgebungsvariablen (.env via docker compose)."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


FRITZ_HOST = os.environ.get("FRITZ_HOST", "192.168.178.1")
FRITZ_USER = os.environ.get("FRITZ_USER", "")
FRITZ_PASSWORD = os.environ.get("FRITZ_PASSWORD", "")

# Sekunden zwischen schnellen Messungen (Traffic, Ping, DSL)
POLL_INTERVAL = max(10, _int("POLL_INTERVAL", 30))
# Sekunden zwischen langsamen Abfragen (Geräteliste, WLAN-Details, Firmware)
SLOW_INTERVAL = max(60, _int("SLOW_INTERVAL", 300))
# Minuten zwischen automatischen Speedtests (0 = aus)
SPEEDTEST_INTERVAL_MIN = max(0, _int("SPEEDTEST_INTERVAL_MIN", 60))
SPEEDTEST_DOWN_MB = max(1, _int("SPEEDTEST_DOWN_MB", 25))
SPEEDTEST_UP_MB = max(1, _int("SPEEDTEST_UP_MB", 10))

PING_TARGETS = [t.strip() for t in os.environ.get("PING_TARGETS", "1.1.1.1,8.8.8.8").split(",") if t.strip()]
RETENTION_DAYS = max(1, _int("RETENTION_DAYS", 30))
DB_PATH = os.environ.get("DB_PATH", "/data/router-stats.db")
