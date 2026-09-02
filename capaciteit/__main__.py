"""capaciteit entrypoint."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from capaciteit.config import Config
from capaciteit.evcc import EvccClient
from capaciteit.loop import Controller
from capaciteit.store import Store
from capaciteit.web import serve

log = logging.getLogger("capaciteit")


async def main() -> int:
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")

    if cfg.dry_run:
        log.info("DRY RUN — decisions are computed and shown, nothing is sent to evcc")
    else:
        log.warning("LIVE — evcc setpoints and battery mode will be changed")

    store = Store()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = await serve(cfg, store)
    async with EvccClient(cfg.evcc_url, cfg.evcc_api_key) as client:
        if not await client.health():
            log.warning("evcc at %s not reachable yet — retrying in the loop",
                        cfg.evcc_url)
        controller = Controller(cfg, client, store)
        await controller.run(stop)

    await runner.cleanup()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
