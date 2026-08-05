"""Apply database migrations in order to DATABASE_URL.

Idempotent: records applied files in a `schema_migrations` table and skips ones
already recorded, so it's safe to run on every deploy. This is how a production
database bootstraps or updates its schema — dev/test build the schema via the
pytest harness (tests/conftest.py) instead.

Usage (from the repo root, with DATABASE_URL set in the environment or .env):
    python scripts/apply_migrations.py            # apply pending migrations
    python scripts/apply_migrations.py --dry-run  # list pending, apply nothing
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def apply(dry_run: bool) -> None:
    load_dotenv()  # does not override an already-exported DATABASE_URL
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (export it or put it in .env)")

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        sys.exit(f"no migrations found in {MIGRATIONS_DIR}")

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename   TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
               )"""
        )
        applied = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        pending = [f for f in files if f.name not in applied]

        if not pending:
            print(f"up to date — all {len(files)} migration(s) already applied")
            return

        print(f"pending ({len(pending)}): {', '.join(f.name for f in pending)}")
        if dry_run:
            print("dry run — nothing applied")
            return

        for f in pending:
            async with conn.transaction():
                # encoding is explicit on purpose: read_text() defaults to the
                # host locale (cp1252 on Windows), which silently mis-decodes
                # these UTF-8 files instead of raising. See
                # tests/test_migration_encoding.py.
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", f.name
                )
            print(f"  applied {f.name}")
        print(f"done — {len(pending)} migration(s) applied")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="list pending migrations without applying them")
    asyncio.run(apply(parser.parse_args().dry_run))
