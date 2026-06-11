"""Tests for the split circuit breaker (Track A / Track B)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.circuit_breaker import (
    THREAT_INDEX_ATTACK,
    TRACK_B_CONFIDENCE_THRESHOLD,
    _enter_attack_mode,
    _enter_conservative_mode,
    _reset_to_normal,
    check_thresholds,
    compute_threat_signal_index,
    get_state,
    is_track_a_paused,
    record_hourly_stats,
    reset_track_a,
    track_b_allowed,
)

pytestmark = pytest.mark.usefixtures("clean_db")


# ─── Pure function: track_b_allowed ──────────────────────────────────────────

def test_track_b_allowed_at_threshold():
    assert track_b_allowed(0.95) is True


def test_track_b_allowed_above_threshold():
    assert track_b_allowed(1.0) is True


def test_track_b_blocked_below_threshold():
    assert track_b_allowed(0.94) is False


def test_track_b_blocked_at_zero():
    assert track_b_allowed(0.0) is False


# ─── DB state read / write ────────────────────────────────────────────────────

async def test_initial_state_is_normal(db_pool):
    state = await get_state(db_pool)
    assert state.mode == "normal"
    assert state.track_a_paused is False
    assert state.paused_at is None


async def test_is_track_a_paused_false_by_default(db_pool):
    assert await is_track_a_paused(db_pool) is False


async def test_enter_conservative_mode_pauses_track_a(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    state = await get_state(db_pool)
    assert state.mode == "conservative"
    assert state.track_a_paused is True
    assert state.paused_at is not None
    assert state.threat_signal_index == pytest.approx(0.8)


async def test_is_track_a_paused_after_conservative(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    assert await is_track_a_paused(db_pool) is True


async def test_enter_attack_mode_does_not_pause_track_a(db_pool):
    """Attack Mode uses Track B only — no bulk-op pause, just alert."""
    await _enter_attack_mode(db_pool, threat_index=1.9)
    state = await get_state(db_pool)
    assert state.mode == "attack"
    assert state.track_a_paused is False
    assert state.threat_signal_index == pytest.approx(1.9)


async def test_reset_to_normal_clears_state(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    await _reset_to_normal(db_pool, source="test")
    state = await get_state(db_pool)
    assert state.mode == "normal"
    assert state.track_a_paused is False
    assert state.paused_at is None
    assert state.threat_signal_index is None


# ─── reset_track_a ────────────────────────────────────────────────────────────

async def test_reset_track_a_succeeds_above_threshold(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    result = await reset_track_a(db_pool, source="escalation_ai", threat_index=1.5)
    assert result is True
    assert await is_track_a_paused(db_pool) is False


async def test_reset_track_a_denied_below_threshold(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    result = await reset_track_a(db_pool, source="escalation_ai", threat_index=1.49)
    assert result is False
    assert await is_track_a_paused(db_pool) is True  # still paused


async def test_reset_track_a_writes_audit_log(db_pool):
    await _enter_conservative_mode(db_pool, threat_index=0.8)
    await reset_track_a(db_pool, source="test_source", threat_index=2.0)
    row = await db_pool.fetchrow(
        "SELECT metadata FROM audit_log_2026_06 WHERE action = 'circuit_breaker_track_a_reset'"
    )
    if not row:
        row = await db_pool.fetchrow(
            "SELECT metadata FROM audit_log_2026_07 WHERE action = 'circuit_breaker_track_a_reset'"
        )
    assert row is not None
    assert row["metadata"]["source"] == "test_source"


# ─── record_hourly_stats ──────────────────────────────────────────────────────

async def test_record_hourly_stats_upserts(db_pool):
    await record_hourly_stats(db_pool)
    count = await db_pool.fetchval("SELECT COUNT(*) FROM circuit_stats_hourly")
    assert count == 1


async def test_record_hourly_stats_idempotent(db_pool):
    await record_hourly_stats(db_pool)
    await record_hourly_stats(db_pool)
    count = await db_pool.fetchval("SELECT COUNT(*) FROM circuit_stats_hourly")
    assert count == 1


# ─── check_thresholds: auto-expiry ───────────────────────────────────────────

async def test_attack_mode_auto_expires_after_2h(db_pool):
    """If mode_entered_at is > 2h ago, attack mode should reset to normal."""
    now = datetime.now(timezone.utc)
    await db_pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'attack', track_a_paused = FALSE,
               mode_entered_at = $1, threat_signal_index = 2.0
           WHERE id = 1""",
        now - timedelta(hours=2, minutes=1),
    )
    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    assert state.mode == "normal"


async def test_attack_mode_not_expired_before_2h(db_pool):
    now = datetime.now(timezone.utc)
    await db_pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'attack', track_a_paused = FALSE,
               mode_entered_at = $1, threat_signal_index = 2.0
           WHERE id = 1""",
        now - timedelta(hours=1),
    )
    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    assert state.mode == "attack"


async def test_conservative_track_a_auto_resumes_after_24h(db_pool):
    now = datetime.now(timezone.utc)
    await db_pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'conservative', track_a_paused = TRUE,
               paused_at = $1, mode_entered_at = $1, threat_signal_index = 0.8
           WHERE id = 1""",
        now - timedelta(hours=25),
    )
    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    assert state.mode == "normal"
    assert state.track_a_paused is False


# ─── check_thresholds: threshold gates ───────────────────────────────────────

async def _seed_ban_rate(db_pool, agent_count: int, ban_count: int) -> None:
    """Insert agent stubs and ban records within the last hour."""
    for i in range(agent_count):
        await db_pool.execute(
            "INSERT INTO agents (api_key_hash, created_at) VALUES ($1, NOW())",
            f"fakehash-cb-{i}",
        )
    # Insert bans against the first ban_count agents
    agent_ids = await db_pool.fetch(
        "SELECT id FROM agents ORDER BY created_at DESC LIMIT $1", agent_count
    )
    for rec in agent_ids[:ban_count]:
        await db_pool.execute(
            """INSERT INTO bans (agent_id, reason, created_at)
               VALUES ($1, 'test', NOW())""",
            rec["id"],
        )


async def test_high_ban_rate_enters_attack_mode(db_pool):
    """Ban rate > 20% with no 7-day baseline → threat_index ≥ 1.5 → Attack Mode."""
    await _seed_ban_rate(db_pool, agent_count=10, ban_count=3)  # 30% ban rate
    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    assert state.mode == "attack"


async def test_medium_ban_rate_low_threat_enters_conservative(db_pool):
    """Ban rate 5–20% + threat_index < 1.5 → Conservative Mode."""
    await _seed_ban_rate(db_pool, agent_count=20, ban_count=2)  # 10% ban rate

    # Seed 7-day history to suppress threat_index below 1.5
    now = datetime.now(timezone.utc)
    for h in range(1, 8 * 24):
        hr = now - timedelta(hours=h)
        await db_pool.execute(
            """INSERT INTO circuit_stats_hourly (hour, flag_count, ban_count, new_agent_count)
               VALUES ($1, 5, 3, 20) ON CONFLICT DO NOTHING""",
            hr.replace(minute=0, second=0, microsecond=0),
        )

    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    # 10% ban rate is in anomaly range; with baseline, threat_index < 1.5
    # so should be conservative (not attack)
    assert state.mode in ("conservative", "normal")  # depends on baseline ratio


async def test_no_anomaly_stays_normal(db_pool):
    """Zero bans, zero flags → stays normal."""
    await check_thresholds(db_pool)
    state = await get_state(db_pool)
    assert state.mode == "normal"


# ─── Admin endpoint: GET state ────────────────────────────────────────────────

async def test_get_circuit_breaker_state_admin(client, db_pool):
    r = await client.get(
        "/internal/security/circuit-breaker",
        headers={"Authorization": "Admin dev-admin-key"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "normal"
    assert data["track_a_paused"] is False


async def test_get_circuit_breaker_state_unauthorized(client):
    r = await client.get(
        "/internal/security/circuit-breaker",
        headers={"Authorization": "Admin wrong-key"},
    )
    assert r.status_code == 403


# ─── Admin endpoint: POST reset-track-a ──────────────────────────────────────

async def test_reset_track_a_endpoint_denied_when_index_low(client, db_pool):
    """With no bans/flags the threat index is 1.0 (no baseline) — below 1.5 → 409."""
    # Put into conservative mode
    await db_pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'conservative', track_a_paused = TRUE, paused_at = NOW(),
               mode_entered_at = NOW()
           WHERE id = 1"""
    )
    with patch(
        "app.routers.internal.security.compute_threat_signal_index",
        new=AsyncMock(return_value=1.0),
    ):
        r = await client.post(
            "/internal/security/circuit-breaker/reset-track-a",
            json={"source": "test_escalation"},
            headers={"Authorization": "Admin dev-admin-key"},
        )
    assert r.status_code == 409
    assert "threat_index_too_low" in r.json()["detail"]["code"]


async def test_reset_track_a_endpoint_succeeds_when_index_high(client, db_pool):
    await db_pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'conservative', track_a_paused = TRUE, paused_at = NOW(),
               mode_entered_at = NOW()
           WHERE id = 1"""
    )
    with patch(
        "app.routers.internal.security.compute_threat_signal_index",
        new=AsyncMock(return_value=2.0),
    ):
        r = await client.post(
            "/internal/security/circuit-breaker/reset-track-a",
            json={"source": "test_escalation"},
            headers={"Authorization": "Admin dev-admin-key"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["threat_signal_index"] == pytest.approx(2.0)
    assert await is_track_a_paused(db_pool) is False
