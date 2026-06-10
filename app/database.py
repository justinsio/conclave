import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=20)
    return _pool


async def init_pool(database_url: str | None = None) -> asyncpg.Pool:
    global _pool
    url = database_url or settings.database_url
    _pool = await asyncpg.create_pool(url, min_size=2, max_size=20)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
