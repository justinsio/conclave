from __future__ import annotations

from datetime import datetime
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin
from app.database import get_pool
from app.services.circuit_breaker import (
    CircuitBreakerState,
    compute_threat_signal_index,
    get_state,
    reset_track_a,
)

router = APIRouter(prefix="/internal/security", tags=["internal-security"])


class CircuitBreakerStateResponse(BaseModel):
    mode: str
    track_a_paused: bool
    paused_at: Optional[datetime]
    mode_entered_at: datetime
    threat_signal_index: Optional[float]
    last_checked_at: Optional[datetime]


class ResetTrackARequest(BaseModel):
    source: str


class ResetTrackAResponse(BaseModel):
    success: bool
    threat_signal_index: float
    message: str


@router.get("/circuit-breaker", response_model=CircuitBreakerStateResponse)
async def get_circuit_breaker_state(
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    state: CircuitBreakerState = await get_state(pool)
    return CircuitBreakerStateResponse(
        mode=state.mode,
        track_a_paused=state.track_a_paused,
        paused_at=state.paused_at,
        mode_entered_at=state.mode_entered_at,
        threat_signal_index=state.threat_signal_index,
        last_checked_at=state.last_checked_at,
    )


@router.post("/circuit-breaker/reset-track-a", response_model=ResetTrackAResponse)
async def reset_track_a_endpoint(
    body: ResetTrackARequest,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    threat_index = await compute_threat_signal_index(pool)
    success = await reset_track_a(pool, source=body.source, threat_index=threat_index)
    if not success:
        raise HTTPException(
            409,
            detail={
                "code": "threat_index_too_low",
                "message": f"Track A reset denied — threat_signal_index {threat_index:.2f} < 1.5. "
                           "Real attack not confirmed; confused AI suppression still active.",
            },
        )
    return ResetTrackAResponse(
        success=True,
        threat_signal_index=threat_index,
        message="Track A resumed. Audit log written.",
    )
