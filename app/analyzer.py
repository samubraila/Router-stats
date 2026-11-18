"""Regelbasierte Analyse: erkennt typische Ursachen für langsames Internet und
gibt konkrete Empfehlungen (auf Deutsch) zurück."""
from __future__ import annotations

import time

LEVEL_ORDER = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
PENALTY = {"crit": 25, "warn": 10, "info": 2, "ok": 0}
NON_OVERLAPPING_24 = {1, 5, 6, 9, 11, 13}  # 1/6/11 oder 1/5/9/13


def de(v, d: int = 1) -> str:
    """Zahl im deutschen Format (Komma)."""
    return f"{v:.{d}f}".replace(".", ",")


def _f(fid, level, area, title, detail="", action=""):
    return {"id": fid, "level": level, "area": area, "title": title, "detail": detail, "action": action}


def analyze(state: dict, store) -> dict:
    out: list[dict] = []
    dsl = state.get("dsl") or {}
    link = state.get("link") or {}
    device = state.get("device") or {}
    radios = state.get("wlan") or []
    extras = state.get("wlan_extras") or {}
    devices = state.get("devices") or []
    conn = state.get("connection") or {}

    # ------------------------------------------------------------------ DSL
    if dsl.get("status") == "Up":
        sd, su = (dsl.get("sync_down_kbps") or 0) / 1000, (dsl.get("sync_up_kbps") or 0) / 1000
        md, mu = (dsl.get("max_down_kbps") or 0) / 1000, (dsl.get("max_up_kbps") or 0) / 1000
        snr = dsl.get("snr_down_db") or 0
        if snr < 6:
            out.append(_f("dsl_snr", "crit" if snr < 3 else "warn", "DSL",
                          f"Niedriger Störabstand ({de(snr)} dB)",
                          "Unter 6 dB wird die DSL-Leitung instabil (Abbrüche, Resyncs, Fehler).",
                          "Kabel von der TAE-Dose zur FRITZ!Box prüfen (kurz, direkt, ohne Verlängerung). "
                          "Bei dauerhaft niedrigen Werten den Anbieter kontaktieren."))
        else:
            out.append(_f("dsl_ok", "ok", "DSL", f"DSL-Leitung stabil: {de(sd)} / {de(su)} Mbit/s",
                          f"Störabstand {de(snr)} dB, Dämpfung {de(dsl.get('att_down_db', 0))} dB."))
        if md and sd and md > sd * 1.15:
            out.append(_f("dsl_reserve", "info", "DSL",
                          f"Die Leitung könnte mehr: bis {md:.0f} Mbit/s möglich, gebucht {sd:.0f} Mbit/s",
                          f"Die FRITZ!Box meldet {md:.0f}/{mu:.0f} Mbit/s als erreichbar. Begrenzt wird durch den Tarif.",
                          "Mehr Geschwindigkeit gibt es nur über einen schnelleren Tarif beim Anbieter."))
        crc24 = store.stats("dsl_crc_delta", 24)
        if crc24 and crc24["n"] > 10:
            total = crc24["avg"] * crc24["n"]
            if total > 500:
                out.append(_f("dsl_crc", "warn", "DSL", f"Viele DSL-Übertragungsfehler ({total:.0f} CRC in 24 h)",
                              "Gestörte Pakete müssen neu gesendet werden und bremsen die Verbindung.",
                              "Leitungskabel und Steckverbindungen prüfen, Störquellen (Netzteile) entfernen."))
    elif link.get("access_type") and link.get("status") != "Up":
        out.append(_f("link_down", "crit", "Internet", "Keine Verbindung zum Anbieter",
                      f"Physischer Link-Status: {link.get('status')}"))

    if conn and conn.get("status") not in (None, "Connected"):
        out.append(_f("conn_down", "crit", "Internet", "Internetverbindung nicht aufgebaut",
                      f"Status: {conn.get('status')}, letzter Fehler: {conn.get('last_error')}"))

    # ---------------------------------------------------------- Speedtest
    tests = [t for t in store.speedtests(days=7) if t.get("down")]
    if tests:
        recent = tests[-5:]
        avg_down = sum(t["down"] for t in recent) / len(recent)
        sync = recent[-1].get("sync_down")
        if sync:
            # Verkehr anderer Geräte während des Tests mitzählen - der Test teilt sich die Leitung
            bg = sum(t.get("background") or 0 for t in recent) / len(recent)
            ratio = (avg_down + bg) / sync
            bg_txt = f" Andere Geräte nutzten zeitgleich ca. {bg:.0f} Mbit/s (mitgerechnet)." if bg >= 1 else ""
            if ratio < 0.6:
                out.append(_f("speed_low", "warn", "Internet",
                              f"Speedtest nur {avg_down:.0f} von {sync:.0f} Mbit/s ({ratio * 100:.0f} % genutzt)",
                              "Gemessen vom Docker-Host aus." + bg_txt,
                              "Liegt die DSL-Leitung in Ordnung, ist meist das WLAN der Engpass: "
                              "Host per LAN-Kabel testen, 5 GHz nutzen, näher an den Router."))
            else:
                out.append(_f("speed_ok", "ok", "Internet",
                              f"Speedtest: {avg_down:.0f} Mbit/s, Leitung zu {ratio * 100:.0f} % genutzt",
                              "Ø der letzten Tests. Etwa 85-95 % der Sync-Rate ist bei DSL das Maximum." + bg_txt))

    # ------------------------------------------------------------- Latenz
    r1 = store.stats("ping_router_avg", 1)
    rmax = store.stats("ping_router_max", 1)
    rloss = store.stats("ping_router_loss", 1)
    if r1 and rmax:
        spikes = rmax["avg"]
        if (rloss and rloss["avg"] > 1) or r1["avg"] > 15 or spikes > 60:
            out.append(_f("lan_latency", "warn", "WLAN",
                          f"Instabile Verbindung zum Router (Ø {de(r1['avg'])} ms, Spitzen Ø {spikes:.0f} ms)",
                          "Zum Router sollten es konstant 1-5 ms sein. Spitzen deuten auf einen überlasteten "
                          "oder gestörten Funkkanal bzw. schwaches Signal hin.",
                          "Docker-Host per LAN anschließen oder 5 GHz nutzen; Kanal-Einstellungen prüfen."))
        else:
            out.append(_f("lan_ok", "ok", "WLAN", f"Verbindung zum Router stabil (Ø {de(r1['avg'])} ms)"))
    i1 = store.stats("ping_inet_avg", 1)
    iloss = store.stats("ping_inet_loss", 1)
    if i1:
        if (iloss and iloss["avg"] > 2) or i1["avg"] > 60:
            out.append(_f("inet_latency", "warn", "Internet",
                          f"Hohe Internet-Latenz/Paketverlust (Ø {i1['avg']:.0f} ms, Verlust {de(iloss['avg'] if iloss else 0)} %)",
                          "Ursache kann eine voll ausgelastete Leitung (Uploads, Backups) oder ein Problem beim Anbieter sein.",
                          "Auslastung im Diagramm prüfen; in der FRITZ!Box unter Internet > Filter > Priorisierung "
                          "wichtige Geräte bevorzugen."))

    # ---------------------------------------------------------- Auslastung
    sync_d = (dsl.get("sync_down_kbps") or link.get("max_down_kbps") or 0) / 1000
    rx = store.stats("wan_rx_mbps", 1)
    if sync_d and rx and rx["n"] > 10:
        busy = store.count_above("wan_rx_mbps", 1, sync_d * 0.85)
        if busy / rx["n"] > 0.2:
            out.append(_f("wan_full", "warn", "Internet",
                          f"Leitung oft voll ausgelastet ({busy / rx['n'] * 100:.0f} % der letzten Stunde > 85 %)",
                          "Wenn ein Gerät die Leitung auslastet (Updates, Streaming in 4K, Downloads), "
                          "wird es für alle anderen langsam.",
                          "In der FRITZ!Box unter Heimnetz > Netzwerk sehen, welches Gerät viel lädt; "
                          "ggf. Priorisierung einrichten."))

    # ---------------------------------------------------------------- WLAN
    for r in radios:
        if not r.get("enabled") or r.get("type") == "guest":
            continue
        ch = r.get("channel")
        if r.get("band") == "2,4 GHz" and isinstance(ch, int) and ch not in NON_OVERLAPPING_24:
            out.append(_f("wlan24_channel", "warn", "WLAN",
                          f"2,4-GHz-WLAN auf überlappendem Kanal {ch}",
                          "Im 2,4-GHz-Band überlappen benachbarte Kanäle. Nur 1, 6, 11 (bzw. 1, 5, 9, 13) stören sich nicht.",
                          "FRITZ!Box: WLAN > Funkkanal > Kanal 1, 6 oder 11 wählen oder 'Autokanal' aktivieren."))
        if r.get("auto_channel") is False:
            out.append(_f(f"wlan_auto_{r['index']}", "info", "WLAN",
                          f"Autokanal ist aus ({r['band']}, fest Kanal {ch})",
                          "Ein fester Kanal ist ok, solange er frei ist. Ändert sich die Umgebung (Nachbar-WLANs), "
                          "passt die FRITZ!Box ihn nicht automatisch an."))
        if r.get("security") == "11i" and r.get("wpa3_possible"):
            out.append(_f(f"wlan_wpa3_{r['index']}", "info", "Sicherheit",
                          f"WPA3 nicht aktiv ({r['band']})",
                          "Kein Geschwindigkeitsproblem, aber WPA2+WPA3 ist sicherer.",
                          "FRITZ!Box: WLAN > Sicherheit > 'WPA2 + WPA3'."))
    guests = [r for r in radios if r.get("type") == "guest" and r.get("enabled")]
    if guests and all(not g.get("clients") for g in guests):
        out.append(_f("guest_idle", "info", "WLAN", "Gast-WLAN aktiv, aber ohne Geräte",
                      "Jedes zusätzliche WLAN sendet Beacons und kostet etwas Sendezeit.",
                      "Gast-WLAN nur bei Bedarf einschalten (auch mit Zeitlimit möglich)."))
    if extras.get("band_steering") is False:
        out.append(_f("band_steering", "warn", "WLAN", "Band-Steering ist aus",
                      "Ohne Band-Steering bleiben Geräte oft im langsameren 2,4-GHz-Band.",
                      "FRITZ!Box: WLAN > Funkkanal > 'Band Steering' aktivieren."))
    b24 = (extras.get("bands") or {}).get("24ghz") or {}
    if b24.get("mode") and "b" in str(b24.get("modes", {}).get(b24["mode"], "")).replace("wifi", ""):
        out.append(_f("wlan_legacy_b", "warn", "WLAN", "802.11b im 2,4-GHz-Band erlaubt",
                      "Sehr alte 11b-Geräte bremsen alle anderen im Band aus.",
                      "FRITZ!Box: WLAN > Funkkanal > WLAN-Standard 'Wi-Fi 4 + g' (ohne b)."))

    # ------------------------------------------------------------- Geräte
    wl = [d for d in devices if d.get("connection") == "WLAN"]
    weak = [d for d in wl if d.get("signal_pct") is not None and d["signal_pct"] <= 20]
    if weak:
        names = ", ".join(f"{d.get('name') or d.get('ip')} ({d['signal_pct']} %)" for d in weak[:6])
        out.append(_f("weak_clients", "warn", "Geräte", f"{len(weak)} Gerät(e) mit sehr schwachem WLAN-Signal",
                      f"{names}. Schwaches Signal = niedrige Datenrate und viele Wiederholungen. "
                      "Das belegt Sendezeit, die allen anderen fehlt.",
                      "Gerät näher an den Router, Router frei und erhöht aufstellen oder einen "
                      "FRITZ!Repeater (Mesh) bzw. LAN-Kabel / Powerline nutzen."))
    slow = [d for d in wl if d.get("speed_mbit") is not None and 0 < d["speed_mbit"] <= 12
            and d not in weak]
    if slow:
        names = ", ".join(f"{d.get('name') or d.get('ip')} ({d['speed_mbit']} Mbit/s)" for d in slow[:6])
        out.append(_f("slow_clients", "info", "Geräte", f"{len(slow)} Gerät(e) mit sehr niedriger WLAN-Datenrate",
                      f"{names}. Kann bei inaktiven (schlafenden) Geräten normal sein; dauerhaft niedrig bremst es das Band.",
                      "Beobachten. Bleibt der Wert niedrig, Gerät näher an den Router bringen."))
    n24 = sum(1 for d in wl if d.get("band") == "2,4 GHz")
    if wl and n24 > len(wl) / 2 and len(wl) >= 4:
        out.append(_f("many_24", "info", "WLAN", f"{n24} von {len(wl)} WLAN-Geräten im 2,4-GHz-Band",
                      "2,4 GHz ist langsamer und stärker gestört als 5 GHz.",
                      "Moderne Geräte möglichst ins 5-GHz-Band bringen (Band-Steering an, Geräte näher an den Router)."))

    # -------------------------------------------------------------- Router
    if device.get("update_available"):
        out.append(_f("fw_update", "warn", "Router", f"FRITZ!OS-Update verfügbar {device.get('update_version') or ''}".strip(),
                      "Updates beheben Fehler und verbessern oft WLAN und DSL.",
                      "FRITZ!Box: System > Update."))
    elif device.get("firmware"):
        out.append(_f("fw_ok", "ok", "Router", f"FRITZ!OS aktuell ({device['firmware']})"))

    out.sort(key=lambda x: LEVEL_ORDER[x["level"]])
    score = max(0, 100 - sum(PENALTY[x["level"]] for x in out))
    worst = out[0]["level"] if out else "ok"
    return {"score": score, "worst": worst, "items": out, "ts": int(time.time())}
