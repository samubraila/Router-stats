"""Minimaler FRITZ!Box-Client: TR-064 (SOAP, Digest-Auth), IGD-UPnP und die
Weboberflächen-Daten (data.lua) für Werte, die TR-064 nicht liefert.

Alle Funktionen sind lesend - die App verändert keine Router-Einstellungen.
"""
from __future__ import annotations

import hashlib
import re
import threading
import xml.etree.ElementTree as ET
from typing import Any

import httpx

S = "urn:dslforum-org:service"
IGD_WANCOMMON = "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1"

ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:{action} xmlns:u="{service}">{args}</u:{action}></s:Body></s:Envelope>'
)


class FritzError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _num(v: Any) -> Any:
    """'123' -> 123, '1.5' -> 1.5, sonst unverändert."""
    if isinstance(v, str):
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
    return v


class FritzBox:
    def __init__(self, host: str, user: str, password: str, timeout: float = 10.0):
        self.host = host
        self.user = user
        self.password = password
        self.base = f"http://{host}:49000"
        auth = httpx.DigestAuth(user, password) if password else None
        self.client = httpx.Client(timeout=timeout, auth=auth)
        self._sid: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ SOAP
    def soap(self, control: str, service: str, action: str, args: dict | None = None) -> dict:
        arg_xml = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
        body = ENVELOPE.format(action=action, service=service, args=arg_xml)
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SoapAction": f"{service}#{action}",
        }
        with self._lock:
            r = self.client.post(self.base + control, content=body.encode(), headers=headers)
        if r.status_code == 401:
            raise FritzError("Anmeldung fehlgeschlagen (Benutzer/Passwort prüfen)")
        root = ET.fromstring(r.content)
        for el in root.iter():
            if _local(el.tag) == "errorDescription":
                raise FritzError(f"{action}: {el.text}")
        if r.status_code >= 400:
            raise FritzError(f"{action}: HTTP {r.status_code}")
        out: dict[str, Any] = {}
        for el in root.iter():
            if _local(el.tag).endswith("Response"):
                for child in el:
                    key = _local(child.tag)
                    key = key[3:] if key.startswith("New") else key
                    out[key] = _num(child.text or "")
                break
        return out

    def tr(self, svc: str, n: int, control: str, action: str, args: dict | None = None) -> dict:
        return self.soap(control, f"{S}:{svc}:{n}", action, args)

    def _fetch_list(self, path: str) -> list[dict]:
        with self._lock:
            r = self.client.get(self.base + path)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("Item"):
            items.append({_local(c.tag): _num(c.text or "") for c in item})
        return items

    # ------------------------------------------------------------ Abfragen
    def device_info(self) -> dict:
        d = self.tr("DeviceInfo", 1, "/upnp/control/deviceinfo", "GetInfo")
        info = {
            "model": d.get("ModelName"),
            "firmware": d.get("SoftwareVersion"),
            "uptime_s": d.get("UpTime"),
        }
        try:
            u = self.tr("UserInterface", 1, "/upnp/control/userif", "GetInfo")
            info["update_available"] = bool(u.get("UpgradeAvailable"))
            info["update_version"] = u.get("X_AVM-DE_Version") or None
        except Exception:
            info["update_available"] = None
        return info

    def link(self) -> dict:
        d = self.tr("WANCommonInterfaceConfig", 1, "/upnp/control/wancommonifconfig1", "GetCommonLinkProperties")
        return {
            "access_type": d.get("WANAccessType"),
            "status": d.get("PhysicalLinkStatus"),
            "max_down_kbps": (d.get("Layer1DownstreamMaxBitRate") or 0) // 1000,
            "max_up_kbps": (d.get("Layer1UpstreamMaxBitRate") or 0) // 1000,
        }

    def traffic(self) -> dict:
        """Aktuelle WAN-Datenrate (Bytes/s) über IGD-UPnP."""
        d = self.soap("/igdupnp/control/WANCommonIFC1", IGD_WANCOMMON, "GetAddonInfos")
        return {
            "rx_Bps": d.get("ByteReceiveRate"),
            "tx_Bps": d.get("ByteSendRate"),
            "total_rx": d.get("X_AVM_DE_TotalBytesReceived64") or d.get("TotalBytesReceived"),
            "total_tx": d.get("X_AVM_DE_TotalBytesSent64") or d.get("TotalBytesSent"),
        }

    def dsl(self) -> dict:
        i = self.tr("WANDSLInterfaceConfig", 1, "/upnp/control/wandslifconfig1", "GetInfo")
        s = self.tr("WANDSLInterfaceConfig", 1, "/upnp/control/wandslifconfig1", "GetStatisticsTotal")
        return {
            "status": i.get("Status"),
            "sync_down_kbps": i.get("DownstreamCurrRate"),
            "sync_up_kbps": i.get("UpstreamCurrRate"),
            "max_down_kbps": i.get("DownstreamMaxRate"),
            "max_up_kbps": i.get("UpstreamMaxRate"),
            # TR-064 liefert 1/10 dB
            "snr_down_db": (i.get("DownstreamNoiseMargin") or 0) / 10,
            "snr_up_db": (i.get("UpstreamNoiseMargin") or 0) / 10,
            "att_down_db": (i.get("DownstreamAttenuation") or 0) / 10,
            "att_up_db": (i.get("UpstreamAttenuation") or 0) / 10,
            "crc_errors": s.get("CRCErrors"),
            "crc_errors_remote": s.get("ATUCCRCErrors"),
            "fec_errors": s.get("FECErrors"),
            "errored_secs": s.get("ErroredSecs"),
            "severely_errored_secs": s.get("SeverelyErroredSecs"),
            "link_retrain": s.get("LinkRetrain"),
        }

    def connection(self) -> dict:
        for svc, ctl in (("WANPPPConnection", "/upnp/control/wanpppconn1"),
                         ("WANIPConnection", "/upnp/control/wanipconnection1")):
            try:
                d = self.tr(svc, 1, ctl, "GetStatusInfo")
                return {
                    "status": d.get("ConnectionStatus"),
                    "uptime_s": d.get("Uptime"),
                    "last_error": d.get("LastConnectionError"),
                }
            except Exception:
                continue
        return {}

    def wlan(self) -> list[dict]:
        radios = []
        for n in (1, 2, 3, 4):
            ctl = f"/upnp/control/wlanconfig{n}"
            try:
                info = self.tr("WLANConfiguration", n, ctl, "GetInfo")
            except Exception:
                continue
            r = {
                "index": n,
                "enabled": bool(info.get("Enable")),
                "status": info.get("Status"),
                "ssid": info.get("SSID"),
                "channel": info.get("Channel"),
                "standard": info.get("Standard"),
                "band": "5 GHz" if str(info.get("X_AVM-DE_FrequencyBand")) == "5000" else "2,4 GHz",
                "security": info.get("BeaconType"),
                "wpa3_possible": "WPA3" in str(info.get("X_AVM-DE_PossibleBeaconTypes", "")),
            }
            try:
                c = self.tr("WLANConfiguration", n, ctl, "GetChannelInfo")
                r["auto_channel"] = bool(c.get("X_AVM-DE_AutoChannelEnabled"))
                r["possible_channels"] = c.get("PossibleChannels")
            except Exception:
                pass
            try:
                a = self.tr("WLANConfiguration", n, ctl, "GetTotalAssociations")
                r["clients"] = a.get("TotalAssociations")
            except Exception:
                pass
            try:
                e = self.tr("WLANConfiguration", n, ctl, "X_AVM-DE_GetWLANExtInfo")
                r["type"] = e.get("X_AVM-DE_APType") or "normal"
            except Exception:
                r["type"] = "normal"
            radios.append(r)
        return radios

    def wlan_devices(self) -> list[dict]:
        p = self.tr("WLANConfiguration", 1, "/upnp/control/wlanconfig1", "X_AVM-DE_GetWLANDeviceListPath")
        path = p.get("X_AVM-DE_WLANDeviceListPath")
        if not path:
            return []
        out = []
        for it in self._fetch_list(path):
            ch = it.get("AssociatedDeviceChannel") or 0
            out.append({
                "ip": it.get("AssociatedDeviceIPAddress"),
                "mac": it.get("AssociatedDeviceMACAddress"),
                "speed_mbit": it.get("X_AVM-DE_Speed"),
                "signal_pct": it.get("X_AVM-DE_SignalStrength"),
                "channel": ch,
                "band": "5 GHz" if isinstance(ch, int) and ch > 14 else "2,4 GHz",
                "width_mhz": it.get("X_AVM-DE_ChannelWidth"),
                "guest": bool(it.get("AssociatedDeviceGuest")),
            })
        return out

    def hosts(self) -> list[dict]:
        p = self.tr("Hosts", 1, "/upnp/control/hosts", "X_AVM-DE_GetHostListPath")
        path = p.get("X_AVM-DE_HostListPath")
        if not path:
            return []
        out = []
        for it in self._fetch_list(path):
            if str(it.get("Active")) != "1":
                continue
            if not it.get("IPAddress") or it.get("IPAddress") == self.host:
                continue
            out.append({
                "name": it.get("HostName"),
                "ip": it.get("IPAddress"),
                "mac": it.get("MACAddress"),
                "interface": it.get("InterfaceType") or "",
                "speed_mbit": it.get("X_AVM-DE_Speed"),
                "guest": bool(it.get("X_AVM-DE_Guest")),
            })
        return out

    # -------------------------------------------------- Weboberfläche (lua)
    def _login(self) -> str:
        r = self.client.get(f"http://{self.host}/login_sid.lua?version=2")
        ch = re.search(r"<Challenge>(.*?)</Challenge>", r.text).group(1)
        if ch.startswith("2$"):
            _, i1, s1, i2, s2 = ch.split("$")
            h1 = hashlib.pbkdf2_hmac("sha256", self.password.encode(), bytes.fromhex(s1), int(i1))
            h2 = hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(s2), int(i2))
            response = f"{s2}${h2.hex()}"
        else:  # alte MD5-Variante
            md5 = hashlib.md5(f"{ch}-{self.password}".encode("utf-16-le")).hexdigest()
            response = f"{ch}-{md5}"
        r = self.client.post(f"http://{self.host}/login_sid.lua?version=2",
                             data={"username": self.user, "response": response})
        sid = re.search(r"<SID>(.*?)</SID>", r.text).group(1)
        if set(sid) == {"0"}:
            raise FritzError("Login an der Weboberfläche fehlgeschlagen")
        return sid

    def _data(self, page: str) -> dict:
        for attempt in (1, 2):
            if not self._sid:
                self._sid = self._login()
            r = self.client.post(f"http://{self.host}/data.lua",
                                 data={"xhr": 1, "sid": self._sid, "page": page, "lang": "de", "xhrId": "all"})
            try:
                return r.json().get("data", {})
            except ValueError:
                self._sid = None  # Session abgelaufen -> neu anmelden
        raise FritzError(f"data.lua {page} nicht lesbar")

    def wlan_extras(self) -> dict:
        """Band-Steering, Kanalwahl, Sendeleistung, WLAN-Modus pro Band."""
        d = self._data("chan")
        out = {
            "band_steering": (d.get("bandSteering") or {}).get("on"),
            "channel_mode": d.get("channelSelectMode"),
            "power_level": (d.get("powerLevel") or {}).get("current"),
            "bands": {},
        }
        for band in ("24ghz", "5ghz"):
            b = d.get(band) or {}
            if not b:
                continue
            out["bands"][band] = {
                "active": b.get("active"),
                "channel": b.get("currentChannel"),
                "mode": b.get("currentWlanMode"),
                "modes": {m.get("value"): m.get("standardTextKey") for m in b.get("wlanModes", [])},
                "neighbors": b.get("usedChannels") or [],
            }
        return out
