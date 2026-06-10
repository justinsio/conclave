import hashlib
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, HTTPException

from app.database import get_pool


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def require_seed_agent(
    authorization: Annotated[str, Header()],
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Invalid auth header")

    api_key = authorization.removeprefix("Bearer ")
    key_hash = hash_api_key(api_key)

    agent = await pool.fetchrow(
        """SELECT id, is_seed, calibration_score, calibration_sample_size
           FROM agents
           WHERE api_key_hash = $1
             AND (banned_until IS NULL OR banned_until < NOW())""",
        key_hash,
    )

    if not agent:
        raise HTTPException(403, "Invalid API key")
    if not agent["is_seed"]:
        raise HTTPException(403, "Seed agents only")

    return dict(agent)
