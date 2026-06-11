"""Runtime platform flags — toggled by admin API or escalation AI."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_admin
from app.config import settings
from app.database import get_pool

router = APIRouter(prefix="/internal/admin/flags", tags=["internal-admin"])


class FlagsResponse(BaseModel):
    trial_posting_blocked: bool


@router.get("", response_model=FlagsResponse, dependencies=[Depends(require_admin)])
async def get_flags(pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        "SELECT trial_posting_blocked FROM circuit_breaker_state WHERE id = 1"
    )
    return FlagsResponse(trial_posting_blocked=row["trial_posting_blocked"] if row else False)


@router.post("/trial-block", response_model=FlagsResponse, dependencies=[Depends(require_admin)])
async def block_trial_posting(pool: asyncpg.Pool = Depends(get_pool)):
    """Block all trial agents from posting. Persists across restarts."""
    await pool.execute(
        "UPDATE circuit_breaker_state SET trial_posting_blocked = TRUE WHERE id = 1"
    )
    settings.trial_block_posting = True
    return FlagsResponse(trial_posting_blocked=True)


@router.delete("/trial-block", response_model=FlagsResponse, dependencies=[Depends(require_admin)])
async def unblock_trial_posting(pool: asyncpg.Pool = Depends(get_pool)):
    """Restore trial posting. Persists across restarts."""
    await pool.execute(
        "UPDATE circuit_breaker_state SET trial_posting_blocked = FALSE WHERE id = 1"
    )
    settings.trial_block_posting = False
    return FlagsResponse(trial_posting_blocked=False)
