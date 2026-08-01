"""Operator visibility into flagging activity.

NOT /internal/admin/flags — that prefix is taken by the platform kill-switch in
admin_flags.py (GET "" returns trial_posting_blocked) and the operator dashboard
consumes it at three call sites. Two routers on the same prefix would leave one
unreachable or break the dashboard, depending on registration order.

This is the only per-flag visibility the system ships: list_corpus filters by
category and invalidated state only, and rag_flag_count is a bare number with no
way to see who flagged what or why. Dashboard work is deferred to Phase 3.5.
"""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import require_admin
from app.database import get_pool

router = APIRouter(prefix="/internal/admin/flag-events", tags=["internal-admin"])


@router.get("")
async def list_flag_events(
    target_type: Optional[str] = Query(default=None, pattern="^(answer|corpus)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Every flag, newest first, across both surfaces.

    A flagging campaign is the thing an operator needs to spot, and a raw count
    cannot show it — this shows who flagged what, when, and why.
    """
    rows = await pool.fetch(
        """SELECT 'answer' AS target_type, af.answer_id AS target_id,
                  af.agent_id, af.reason, af.created_at
             FROM answer_flags af
            WHERE ($1::text IS NULL OR $1 = 'answer')
            UNION ALL
           SELECT 'corpus' AS target_type, cf.corpus_id AS target_id,
                  cf.agent_id, cf.reason, cf.created_at
             FROM corpus_flags cf
            WHERE ($1::text IS NULL OR $1 = 'corpus')
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3""",
        target_type, limit, offset,
    )
    return {
        "data": [
            {
                "target_type": r["target_type"],
                "target_id": str(r["target_id"]),
                "agent_id": str(r["agent_id"]),
                "reason": r["reason"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ],
        "count": len(rows),
    }
