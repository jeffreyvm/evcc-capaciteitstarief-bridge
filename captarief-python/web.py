"""Laag 1 — alleen-lezen dashboard.

Toont wat de daemon net besloten heeft. Schrijft nergens naartoe: geen
actuatiepad, alleen `SharedState.snapshot()` uitgeserveerd als JSON en als
een HTML-pagina die dat elke paar seconden opnieuw ophaalt.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import SharedState


def maak_app(state: SharedState) -> FastAPI:
    app = FastAPI(title="capbudget dashboard")

    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGINA

    return app


_PAGINA = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>capbudget</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; padding: 2rem; min-height: 100vh; box-sizing: border-box;
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  }
  h1 { font-size: 1.1rem; font-weight: 600; color: #8b949e; margin: 0 0 1.5rem; }
  .envelope { font-size: 3.5rem; font-weight: 700; color: #6d5dfc; line-height: 1; }
  .envelope span { font-size: 1.5rem; color: #8b949e; font-weight: 400; }
  .reden { margin: 0.5rem 0 2rem; color: #e6edf3; }
  .stale .envelope { color: #8b949e; }
  .stale .reden::after { content: " — geen recente tik, mogelijk gestopt"; color: #f85149; }
  table { border-collapse: collapse; width: 100%; max-width: 32rem; }
  td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #21262d; }
  td:first-child { color: #8b949e; }
  td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
  .voet { margin-top: 1.5rem; color: #484f58; font-size: 0.8rem; }

  @media (max-width: 480px) {
    body { padding: 1.25rem; }
    .envelope { font-size: 2.5rem; }
    .envelope span { font-size: 1.1rem; }
    table { max-width: 100%; }
    td { padding: 0.5rem 0.25rem; font-size: 0.9rem; }
  }
</style>
</head>
<body>
  <h1>capbudget — laag 1 budgetteur</h1>
  <div class="envelope" id="envelope">— <span>W</span></div>
  <p class="reden" id="reden">wachten op eerste tik…</p>
  <table id="tabel"></table>
  <p class="voet" id="voet"></p>

<script>
const VELDEN = [
  ["doel_w", "doel (W)"],
  ["maandpiek_w", "maandpiek (W)"],
  ["budget_wh", "kwartierbudget (Wh)"],
  ["verbruikt_wh", "verbruikt dit kwartier (Wh)"],
  ["rest_wh", "rest dit kwartier (Wh)"],
  ["resterend_sec", "resterend (s)"],
  ["toegestaan_gemiddelde_w", "toegestaan gemiddeld (W)"],
  ["p_net_w", "netvermogen (W)"],
  ["p_ev_w", "laadvermogen (W)"],
  ["failsafe", "failsafe"],
  ["gepubliceerd", "deze tik gepubliceerd"],
];

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "ja" : "nee";
  return v;
}

async function ververs() {
  let data;
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    return;
  }

  const body = document.body;
  const laatsteTik = data.laatste_tik_ts;
  const stale = !laatsteTik || (Date.now() / 1000 - laatsteTik) > 90;
  body.classList.toggle("stale", stale);

  document.getElementById("envelope").innerHTML =
    (data.envelope_w ?? "—") + " <span>W</span>";
  document.getElementById("reden").textContent = data.reden ?? "nog geen besluit";

  const tabel = document.getElementById("tabel");
  tabel.innerHTML = VELDEN
    .filter(([sleutel]) => data[sleutel] !== undefined)
    .map(([sleutel, label]) => `<tr><td>${label}</td><td>${fmt(data[sleutel])}</td></tr>`)
    .join("");

  document.getElementById("voet").textContent = laatsteTik
    ? "laatste tik: " + new Date(laatsteTik * 1000).toLocaleTimeString("nl-BE")
    : "";
}

ververs();
setInterval(ververs, 5000);
</script>
</body>
</html>
"""
