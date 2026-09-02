"""Dashboard and JSON API.

Read-only by design. Everything that changes behaviour lives in the env file,
so the dashboard cannot be used to bypass DRY_RUN or move a setpoint — one
fewer thing to secure on a LAN-exposed port.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from capaciteit.config import Config
from capaciteit.store import Store

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def build_app(cfg: Config, store: Store) -> web.Application:
    app = web.Application()

    async def index(_request):
        return web.FileResponse(STATIC / "index.html")

    async def api_state(_request):
        return web.json_response(store.payload(cfg.public()), dumps=_dumps)

    async def api_health(_request):
        healthy = store.latest is not None
        return web.json_response(
            {"ok": healthy, "dry_run": cfg.dry_run,
             "last_tick": (store.latest or {}).get("ts")},
            status=200 if healthy else 503)

    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/health", api_health)
    app.router.add_static("/static/", STATIC)
    return app


def _dumps(obj) -> str:
    return json.dumps(obj, default=float)


async def serve(cfg: Config, store: Store) -> web.AppRunner:
    runner = web.AppRunner(build_app(cfg, store))
    await runner.setup()
    site = web.TCPSite(runner, cfg.web_host, cfg.web_port)
    await site.start()
    log.info("dashboard on http://%s:%s", cfg.web_host, cfg.web_port)
    return runner
