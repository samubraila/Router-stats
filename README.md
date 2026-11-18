# Router-Stats

Docker-App, die eine **FRITZ!Box** dauerhaft überwacht und analysiert, warum das Internet langsam ist.
Sie zeigt ein Dashboard mit Bewertung, konkreten Empfehlungen und Verlaufsdiagrammen.

![Status](https://img.shields.io/badge/Zugriff-nur%20lesend-2a78d6)

## Was wird gemessen?

| Bereich | Daten | Quelle |
|---|---|---|
| DSL-Leitung | Sync-Rate, max. mögliche Rate, Störabstand (SNR), Dämpfung, CRC/FEC-Fehler, Resyncs | TR-064 |
| Internet | aktuelle Auslastung (Download/Upload), Verbindungsdauer, Abbrüche | TR-064 / UPnP |
| Latenz | Ping zum Router und ins Internet (Ø, Spitzen, Jitter, Paketverlust) | Container |
| Speedtest | Download/Upload/Latenz gegen Cloudflare, inkl. Verkehr anderer Geräte während des Tests | Container |
| WLAN | Kanal, Standard, Autokanal, Band-Steering, Sendeleistung, Gast-WLAN, WPA3 | TR-064 / Weboberfläche |
| Geräte | alle aktiven Geräte mit Band (2,4/5 GHz), Signalstärke und Datenrate | TR-064 |
| Router | Modell, FRITZ!OS-Version, verfügbare Updates, Neustarts | TR-064 |

## Was wird erkannt?

Die Analyse ([app/analyzer.py](app/analyzer.py)) prüft unter anderem:

- **überlappender 2,4-GHz-Kanal** (z. B. Kanal 8 statt 1/6/11), Autokanal aus
- **Geräte mit sehr schwachem Signal**, die Sendezeit für alle belegen
- **instabile Verbindung zum Router** (Latenzspitzen = gestörter/überlasteter Funkkanal)
- Speedtest deutlich unter der Sync-Rate → Engpass im Heimnetz statt beim Anbieter
- **Leitung dauerhaft voll ausgelastet** durch andere Geräte
- niedriger DSL-Störabstand, viele CRC-Fehler, Resyncs, Verbindungsabbrüche
- Tarif-Reserve (Leitung könnte mehr als gebucht)
- Band-Steering aus, 802.11b erlaubt, ungenutztes Gast-WLAN, WPA3 aus, Firmware-Update verfügbar

Jeder Befund hat eine Stufe (OK / Hinweis / Warnung / Kritisch) und eine konkrete Empfehlung.
Daraus ergibt sich eine Gesamtbewertung von 0–100.

## Installation

Voraussetzung: Docker mit Docker Compose.

```bash
git clone https://github.com/samubraila/Router-stats.git
cd Router-stats
cp .env.example .env      # FRITZ_USER und FRITZ_PASSWORD eintragen
docker compose up -d --build
```

Dashboard: **http://localhost:8090**

### FRITZ!Box-Zugang

- Benutzername: unter *System > FRITZ!Box-Benutzer*. Wer sich nur mit Passwort anmeldet, hat einen
  automatisch erzeugten Benutzer wie `fritz1234`. Er steht in der Antwort von
  `http://192.168.178.1/login_sid.lua?version=2` unter `<Users>`.
- *Heimnetz > Netzwerk > Netzwerkeinstellungen*: **„Zugriff für Anwendungen zulassen“** (TR-064) und
  **„Statusinformationen über UPnP übertragen“** müssen aktiv sein (Standard).
- Empfohlen: einen eigenen FRITZ!Box-Benutzer nur für diese App anlegen.

## Konfiguration (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `FRITZ_HOST` | `192.168.178.1` | Adresse der FRITZ!Box |
| `FRITZ_USER` / `FRITZ_PASSWORD` | – | Login |
| `WEB_PORT` | `8090` | Port des Dashboards |
| `POLL_INTERVAL` | `30` | Sekunden zwischen Messungen (Traffic, Ping, DSL) |
| `SLOW_INTERVAL` | `300` | Sekunden zwischen Geräte-/WLAN-Abfragen |
| `SPEEDTEST_INTERVAL_MIN` | `60` | Minuten zwischen Speedtests, `0` = aus (ca. 35 MB pro Test) |
| `SPEEDTEST_DOWN_MB` / `SPEEDTEST_UP_MB` | `25` / `10` | Größe der Testdateien |
| `PING_TARGETS` | `1.1.1.1,8.8.8.8` | Ping-Ziele im Internet |
| `RETENTION_DAYS` | `30` | Aufbewahrung der Messdaten |

## Hinweise

- **Nur lesend:** Die App ändert keine Einstellungen an der FRITZ!Box.
- Ping und Speedtest laufen **vom Docker-Host aus**. Hängt der Rechner im WLAN, misst der Speedtest
  das WLAN mit. Für reine Leitungswerte den Host per LAN-Kabel anschließen.
- Die `.env` mit dem Passwort ist in `.gitignore` und wird nicht eingecheckt.
- Messdaten liegen im Docker-Volume `router-stats-data` (SQLite).

## API

| Endpoint | Beschreibung |
|---|---|
| `GET /api/overview` | aktueller Zustand, Befunde, Bewertung |
| `GET /api/history?names=wan_rx_mbps,ping_router_avg&hours=24` | Zeitreihen |
| `GET /api/speedtests?days=7` | Speedtest-Verlauf |
| `POST /api/speedtest` | Speedtest starten |
| `POST /api/refresh` | Geräte/WLAN sofort neu abfragen |
| `GET /api/events` | Ereignisse (Abbrüche, Resyncs, Neustarts) |

## Aufbau

```
app/
  main.py        FastAPI: API + Dashboard
  collector.py   Hintergrund-Sammler (Intervalle, Speedtest, Ereignisse)
  fritz.py       FRITZ!Box-Client (TR-064, UPnP, data.lua)
  probes.py      Ping und Speedtest
  analyzer.py    Regeln und Empfehlungen
  storage.py     SQLite
  static/        Dashboard (HTML/CSS/JS, Chart.js)
```
