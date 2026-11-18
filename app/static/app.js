"use strict";

const $ = (s) => document.querySelector(s);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d }));
const LEVEL = { crit: "Kritisch", warn: "Warnung", info: "Hinweis", ok: "OK" };

let hours = 24;
const charts = {};

function dur(s) {
  if (s == null) return "–";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} T ${h} Std` : h ? `${h} Std ${m} Min` : `${m} Min`;
}
function when(ts) {
  return new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  return r.json();
}

/* ------------------------------------------------------------ Übersicht */
function tile(k, v, unit, sub) {
  return `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}${unit ? ` <small>${esc(unit)}</small>` : ""}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
}

function renderOverview(o, tests) {
  const s = o.state;
  const dev = s.device || {}, dsl = s.dsl || {}, link = s.link || {}, tr = s.traffic || {}, ping = s.ping || {};
  $("#model").textContent = [dev.model, dev.firmware && `FRITZ!OS ${dev.firmware}`, dev.uptime_s && `läuft seit ${dur(dev.uptime_s)}`].filter(Boolean).join(" · ");
  const latest = Math.max(0, ...Object.values(o.updated || {}));
  $("#updated").textContent = latest ? `Stand ${new Date(latest * 1000).toLocaleTimeString("de-DE")}` : "";

  const errs = Object.entries(o.errors || {});
  const banner = $("#errors");
  if (!o.config.has_password) {
    banner.hidden = false;
    banner.innerHTML = "Kein FRITZ!Box-Passwort gesetzt. Bitte <code>FRITZ_USER</code> und <code>FRITZ_PASSWORD</code> in der <code>.env</code> eintragen.";
  } else if (errs.length) {
    banner.hidden = false;
    banner.innerHTML = "Einige Werte konnten nicht gelesen werden: " + errs.map(([k, v]) => `<b>${esc(k)}</b>: ${esc(v)}`).join(" · ");
  } else banner.hidden = true;

  const f = s.findings || { items: [] };
  $("#score").textContent = f.score ?? "–";
  const n = (lvl) => f.items.filter((x) => x.level === lvl).length;
  $("#scoreText").textContent = `${n("crit")} kritisch · ${n("warn")} Warnungen · ${n("info")} Hinweise`;

  const syncD = (dsl.sync_down_kbps || link.max_down_kbps || 0) / 1000;
  const syncU = (dsl.sync_up_kbps || link.max_up_kbps || 0) / 1000;
  const last = [...tests].reverse().find((t) => t.down);
  const devs = s.devices || [];
  const n24 = devs.filter((d) => d.band === "2,4 GHz").length, n5 = devs.filter((d) => d.band === "5 GHz").length;
  const inet = (ping.internet || []).filter((p) => p.avg_ms != null);
  const inetAvg = inet.length ? inet.reduce((a, p) => a + p.avg_ms, 0) / inet.length : null;
  $("#tiles").innerHTML = [
    tile("Leitung (Sync)", `${fmt(syncD, 0)} / ${fmt(syncU, 0)}`, "Mbit/s", dsl.snr_down_db != null ? `Störabstand ${fmt(dsl.snr_down_db)} dB` : esc(link.access_type || "")),
    tile("Letzter Speedtest", last ? `${fmt(last.down, 0)} / ${fmt(last.up, 0)}` : "–", "Mbit/s", last ? `${when(last.ts)} · ${fmt(last.latency, 0)} ms` : o.speedtest_running ? "läuft …" : "noch keiner"),
    tile("Ping Router / Internet", `${fmt(ping.router?.avg_ms, 0)} / ${fmt(inetAvg, 0)}`, "ms",
      ping.router ? `Router max ${fmt(ping.router.max_ms, 0)} ms · Verlust ${fmt(ping.router.loss_pct, 0)} %` : ""),
    tile("Auslastung jetzt", `${fmt((tr.rx_Bps || 0) * 8 / 1e6)} / ${fmt((tr.tx_Bps || 0) * 8 / 1e6)}`, "Mbit/s", syncD ? `${fmt(((tr.rx_Bps || 0) * 8 / 1e6 / syncD) * 100, 0)} % der Leitung` : ""),
    tile("Geräte", devs.length, "", `WLAN: ${n5}× 5 GHz, ${n24}× 2,4 GHz`),
  ].join("");

  $("#findings").innerHTML = f.items.map((x) => `
    <li class="finding ${x.level}">
      <span class="badge"><i aria-hidden="true"></i>${LEVEL[x.level]}</span>
      <div class="t">${esc(x.title)}<span class="area">${esc(x.area)}</span></div>
      ${x.detail ? `<div class="d">${esc(x.detail)}</div>` : ""}
      ${x.action ? `<div class="a"><b>Empfehlung:</b> ${esc(x.action)}</div>` : ""}
    </li>`).join("") || `<li class="muted">Noch keine Daten – die erste Analyse folgt in wenigen Sekunden.</li>`;

  $("#devCount").textContent = devs.length ? `(${devs.length})` : "";
  $("#devices tbody").innerHTML = devs.map((d) => {
    const sig = d.signal_pct;
    const col = sig == null ? "" : sig <= 20 ? css("--critical") : sig <= 40 ? css("--warning") : css("--good");
    const sigHtml = sig == null ? "–" : `<span class="sig"><span class="bar"><span style="width:${sig}%;background:${col}"></span></span>${sig} %</span>`;
    return `<tr><td>${esc(d.name || "–")}${d.guest ? ' <span class="muted small">(Gast)</span>' : ""}</td><td>${esc(d.ip)}</td><td>${esc(d.connection)}</td><td>${esc(d.band || "–")}</td><td>${sigHtml}</td><td class="num">${d.speed_mbit != null ? `${d.speed_mbit} Mbit/s` : "–"}</td></tr>`;
  }).join("");

  $("#radios tbody").innerHTML = (s.wlan || []).map((r) => `<tr>
    <td>${esc(r.ssid)}${r.type === "guest" ? ' <span class="muted small">(Gast)</span>' : ""}</td><td>${esc(r.band)}</td>
    <td class="num">${esc(r.channel)}</td><td>${esc(r.standard ? "802.11" + r.standard : "–")}</td>
    <td>${r.auto_channel == null ? "–" : r.auto_channel ? "an" : "aus"}</td><td class="num">${esc(r.clients ?? "–")}</td></tr>`).join("");

  const ex = s.wlan_extras;
  $("#wlanExtras").innerHTML = ex ? `
    <dt>Band-Steering</dt><dd>${ex.band_steering ? "an" : "aus"}</dd>
    <dt>Kanalwahl</dt><dd>${ex.channel_mode === "manual" ? "manuell" : esc(ex.channel_mode || "–")}</dd>
    <dt>Sendeleistung</dt><dd>Stufe ${esc(ex.power_level ?? "–")} von 5 (1 = maximal)</dd>` : "";

  $("#dsl").innerHTML = dsl.status ? `
    <dt>Status</dt><dd>${esc(dsl.status)}</dd>
    <dt>Sync-Rate</dt><dd>${fmt(dsl.sync_down_kbps / 1000)} / ${fmt(dsl.sync_up_kbps / 1000)} Mbit/s</dd>
    <dt>Max. möglich</dt><dd>${fmt(dsl.max_down_kbps / 1000)} / ${fmt(dsl.max_up_kbps / 1000)} Mbit/s</dd>
    <dt>Störabstand</dt><dd>${fmt(dsl.snr_down_db)} / ${fmt(dsl.snr_up_db)} dB</dd>
    <dt>Dämpfung</dt><dd>${fmt(dsl.att_down_db)} / ${fmt(dsl.att_up_db)} dB</dd>
    <dt>CRC-Fehler</dt><dd>${fmt(dsl.crc_errors, 0)} (Gegenstelle ${fmt(dsl.crc_errors_remote, 0)})</dd>
    <dt>Resyncs</dt><dd>${fmt(dsl.link_retrain, 0)}</dd>
    <dt>Internet verbunden seit</dt><dd>${dur(s.connection?.uptime_s)}</dd>` :
    `<dt>Zugang</dt><dd>${esc(link.access_type || "–")}</dd><dt>Max.</dt><dd>${fmt(link.max_down_kbps / 1000)} / ${fmt(link.max_up_kbps / 1000)} Mbit/s</dd>`;

  $("#btnSpeed").disabled = o.speedtest_running;
  $("#btnSpeed").textContent = o.speedtest_running ? "Speedtest läuft …" : "Speedtest starten";
}

/* ------------------------------------------------------------- Diagramme */
function baseOptions(yMin) {
  const grid = css("--grid"), muted = css("--muted");
  const now = Date.now();
  return {
    responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
    interaction: { mode: "index", intersect: false },
    scales: {
      x: {
        type: "linear", min: now - hours * 3600 * 1000, max: now,
        grid: { display: false }, border: { color: grid },
        ticks: {
          color: muted, maxTicksLimit: 6, maxRotation: 0,
          callback: (v) => new Date(v).toLocaleString("de-DE", hours > 24 ? { day: "2-digit", month: "2-digit" } : { hour: "2-digit", minute: "2-digit" }),
        },
      },
      y: { beginAtZero: yMin === 0, grid: { color: grid }, border: { display: false }, ticks: { color: muted, maxTicksLimit: 5 } },
    },
    plugins: {
      legend: { position: "top", align: "end", labels: { color: css("--text-2"), boxWidth: 12, boxHeight: 2, usePointStyle: false } },
      tooltip: {
        callbacks: {
          title: (items) => items.length ? new Date(items[0].parsed.x).toLocaleString("de-DE") : "",
          label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y, 1)}`,
        },
      },
    },
  };
}
function ds(label, data, color, extra = {}) {
  // Solange wenige Messpunkte da sind, Punkte zeigen - sonst ist eine 1-Punkt-Linie unsichtbar
  const few = data.length < 40;
  return { label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: few ? 3 : 0, pointHoverRadius: 4, tension: 0.25, spanGaps: false, ...extra };
}
function refLine(label, y) {
  if (!y) return null;
  const now = Date.now();
  return ds(label, [{ x: now - hours * 3600 * 1000, y }, { x: now, y }], css("--ref"), { borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 0, tension: 0 });
}
function draw(id, datasets, yMin = 0) {
  datasets = datasets.filter(Boolean);
  if (charts[id]) {
    charts[id].data.datasets = datasets;
    charts[id].options = baseOptions(yMin);
    charts[id].update("none");
  } else {
    charts[id] = new Chart(document.getElementById(id), { type: "line", data: { datasets }, options: baseOptions(yMin) });
  }
}
const pts = (rows, i = 1) => (rows || []).map((r) => ({ x: r[0] * 1000, y: r[i] }));

async function renderCharts(state) {
  const names = "wan_rx_mbps,wan_tx_mbps,ping_router_avg,ping_inet_avg,ping_router_loss,ping_inet_loss,dsl_snr_down,dsl_snr_up,wlan_clients_24,wlan_clients_5";
  const [h, tests] = await Promise.all([
    getJSON(`/api/history?hours=${hours}&names=${names}`),
    getJSON(`/api/speedtests?days=${Math.max(1, hours / 24)}`),
  ]);
  const s1 = css("--series-1"), s2 = css("--series-2");
  const dsl = state.dsl || {}, link = state.link || {};
  const syncD = (dsl.sync_down_kbps || link.max_down_kbps || 0) / 1000;
  draw("cWan", [ds("Download", pts(h.wan_rx_mbps), s1), ds("Upload", pts(h.wan_tx_mbps), s2), refLine("Leitung (Sync)", syncD)]);
  draw("cPing", [ds("Router", pts(h.ping_router_avg), s1), ds("Internet", pts(h.ping_inet_avg), s2)]);
  draw("cLoss", [ds("Router", pts(h.ping_router_loss), s1), ds("Internet", pts(h.ping_inet_loss), s2)]);
  draw("cSnr", [ds("Download", pts(h.dsl_snr_down), s1), ds("Upload", pts(h.dsl_snr_up), s2)]);
  draw("cWlan", [ds("5 GHz", pts(h.wlan_clients_5), s1, { stepped: true, tension: 0 }), ds("2,4 GHz", pts(h.wlan_clients_24), s2, { stepped: true, tension: 0 })]);

  const t = tests.filter((x) => x.down != null);
  const mark = { pointRadius: 4, pointHoverRadius: 6, pointBorderColor: css("--surface"), pointBorderWidth: 2, tension: 0 };
  draw("cSpeed", [
    ds("Download", t.map((x) => ({ x: x.ts * 1000, y: x.down })), s1, mark),
    ds("Upload", t.map((x) => ({ x: x.ts * 1000, y: x.up })), s2, mark),
    refLine("Leitung (Sync)", syncD),
  ]);
  return tests;
}

async function renderEvents() {
  const ev = await getJSON("/api/events?limit=30");
  $("#events").innerHTML = ev.map((e) => `<li class="${e.level}"><span class="muted">${when(e.ts)}</span><span class="badge"><i aria-hidden="true"></i>${LEVEL[e.level] || e.level}</span><span>${esc(e.area)}: ${esc(e.message)}</span></li>`).join("")
    || `<li class="muted">Keine Ereignisse (Abbrüche, Resyncs, Neustarts werden hier protokolliert).</li>`;
}

/* ------------------------------------------------------------------ Start */
async function refresh() {
  try {
    const o = await getJSON("/api/overview");
    const tests = await renderCharts(o.state);
    renderOverview(o, tests);
    renderEvents();
  } catch (e) {
    console.error(e);
  }
}

$("#range").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  hours = Number(b.dataset.h);
  document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b));
  refresh();
});
$("#btnSpeed").addEventListener("click", async () => {
  $("#btnSpeed").disabled = true;
  $("#btnSpeed").textContent = "Speedtest läuft …";
  await fetch("/api/speedtest", { method: "POST" });
  const poll = setInterval(async () => {
    const o = await getJSON("/api/overview");
    if (!o.speedtest_running) { clearInterval(poll); refresh(); }
  }, 3000);
});
$("#btnRefresh").addEventListener("click", async () => {
  $("#btnRefresh").disabled = true;
  await fetch("/api/refresh", { method: "POST" });
  await refresh();
  $("#btnRefresh").disabled = false;
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", refresh);

refresh();
setInterval(refresh, 30000);
