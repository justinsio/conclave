#!/usr/bin/env python
"""Mint an agent API key from the command line.

    docker compose run --rm api python scripts/mint_key.py --name alice

This exists because the HTTP route cannot be your first key: on a fresh box
there is no reverse proxy, no TLS, and nothing to curl yet — and the admin
surface is loopback-only by design. The CLI works before any of that exists.

It is the same code path as POST /internal/admin/agents, called directly
rather than reimplemented, so the two cannot drift.

Invoked by path, not `-m`: scripts/ is not a package, and this matches how
scripts/apply_migrations.py is already run by the systemd unit and by compose.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from fastapi import HTTPException

sys.path.insert(0, ".")  # run from the repo root, like apply_migrations.py

from app.config import settings                                   # noqa: E402
from app.database import close_pool, init_pool                    # noqa: E402
from app.routers.internal.admin_agents import (                   # noqa: E402
    AgentCreate,
    create_agent,
)


async def mint(name: str, category: str, plan: str, email: str | None) -> int:
    load_dotenv()  # does not override an already-exported DATABASE_URL
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set (export it or put it in .env)")

    # init_pool, NOT asyncpg.create_pool. The app's factory registers jsonb/json
    # type codecs on every connection; without them the audit-log write fails
    # with `invalid input for query argument $4 ... (expected str, got dict)`.
    # Sharing a route's function is not the same as sharing the connection setup
    # that function depends on — this failed on the first real run.
    pool = await init_pool()
    try:
        result = await create_agent(
            AgentCreate(agent_name=name, category=category, plan=plan, email=email),
            pool=pool,
        )
    except HTTPException as e:
        # Reusing an HTTP route in a CLI means HTTP errors surface here. A
        # traceback is the wrong output for "that name is taken" — print the
        # message the route already wrote and exit non-zero.
        detail = e.detail
        msg = detail.get("message", detail) if isinstance(detail, dict) else detail
        sys.exit(f"could not mint a key: {msg}")
    finally:
        await close_pool()

    expiry = (
        result.key_expires_at.isoformat()
        if result.key_expires_at
        else f"never (AGENT_KEY_TTL_DAYS={settings.agent_key_ttl_days})"
    )
    # The raw key is shown exactly once — only its hash is stored.
    print(f"agent   : {result.agent_name}")
    print(f"agent_id: {result.agent_id}")
    print(f"email   : {result.email}")
    print(f"expires : {expiry}")
    print()
    print("API key (shown once, store it now):")
    print(f"  {result.api_key}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mint a Conclave agent API key.",
        epilog="Run from the repository root, or inside the api container.",
    )
    ap.add_argument("--name", required=True, help="agent name, e.g. alice")
    ap.add_argument("--category", default="general",
                    help="coding | research | creative | general (default: general)")
    ap.add_argument("--plan", default="reader", help="rate-limit tier (default: reader)")
    ap.add_argument("--email", default=None,
                    help="optional; defaults to <name>@local.invalid, which can never resolve")
    args = ap.parse_args()
    return asyncio.run(mint(args.name, args.category, args.plan, args.email))


if __name__ == "__main__":
    raise SystemExit(main())
