from __future__ import annotations
import logging
import sys

import httpx


def setup_logging(seed_name: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        f"%(asctime)s [{seed_name}] %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def alert_crash(webhook: str | None, seed_name: str, message: str) -> None:
    if not webhook:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(webhook, json={"text": f"⚠️ seed [{seed_name}] crashed: {message}"})
    except Exception:
        logging.getLogger("seed").warning("crash alert failed")
