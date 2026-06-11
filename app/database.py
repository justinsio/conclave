import json

import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url, min_size=2, max_size=20, init=_init_connection
        )
    return _pool


async def init_pool(database_url: str | None = None) -> asyncpg.Pool:
    global _pool
    url = database_url or settings.database_url
    _pool = await asyncpg.create_pool(url, min_size=2, max_size=20, init=_init_connection)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
