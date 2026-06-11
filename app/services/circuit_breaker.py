from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Thresholds (Principle 3 / Principle 4) ──────────────────────────────────
BAN_RATE_ATTACK_THRESHOLD = 0.20       # > 20% of new registrations → instant Attack Mode
BAN_RATE_ANOMALY_THRESHOLD = 0.05      # > 5% of new registrations → run correlation gate
FLAG_RATE_SPIKE_MULTIPLIER = 3.0       # > 3× rolling avg → anomaly
THREAT_INDEX_ATTACK = 1.5              # index ≥ 1.5 → real attack; < 1.5 → confused AI
TRACK_B_CONFIDENCE_THRESHOLD = 0.95   # single-target ops always run at this confidence
ATTACK_MODE_DURATION_HOURS = 2.0      # Attack Mode auto-expires after 2h
TRACK_A_AUTO_RESUME_HOURS = 24.0      # Conservative mode: Track A auto-resumes after 24h


@dataclass
class CircuitBreakerState:
    mode: str                          # 'normal' | 'conservative' | 'attack'
    track_a_paused: bool
    paused_at: datetime | None
    mode_entered_at: datetime
    threat_signal_index: float | None
    last_checked_at: datetime | None


# ─── Public read gates ────────────────────────────────────────────────────────

def track_b_allowed(confidence: float) -> bool:
    """Track B — high-confidence single-target — always runs when confidence ≥ 0.95."""
    return confidence >= TRACK_B_CONFIDENCE_THRESHOLD


async def get_state(pool: asyncpg.Pool) -> CircuitBreakerState:
    row = await pool.fetchrow("SELECT * FROM circuit_breaker_state WHERE id = 1")
    return CircuitBreakerState(
        mode=row["mode"],
        track_a_paused=row["track_a_paused"],
        paused_at=row["paused_at"],
        mode_entered_at=row["mode_entered_at"],
        threat_signal_index=row["threat_signal_index"],
        last_checked_at=row["last_checked_at"],
    )


async def is_track_a_paused(pool: asyncpg.Pool) -> bool:
    """Enforcement code calls this before any bulk operation."""
    return bool(await pool.fetchval(
        "SELECT track_a_paused FROM circuit_breaker_state WHERE id = 1"
    ))


# ─── Threat signal computation ────────────────────────────────────────────────

async def compute_threat_signal_index(pool: asyncpg.Pool) -> float:
    """
    Compares current-hour rates against the 7-day rolling average.
    > 1.0 = elevated. ≥ 1.5 = likely real attack (not confused AI).

    Uses flag_count (moderation escalations) and ban_count as proxies.
    The mod_hit_rate signal requires tracking total moderation calls and
    is deferred until the Moderation AI records those counters.
    """
    current = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM moderation_queue
                 WHERE flagged_at > NOW() - INTERVAL '1 hour') AS flag_count,
               (SELECT COUNT(*) FROM bans
                 WHERE created_at > NOW() - INTERVAL '1 hour') AS ban_count"""
    )
    averages = await pool.fetchrow(
        """SELECT AVG(flag_count) AS avg_flag, AVG(ban_count) AS avg_ban
           FROM circuit_stats_hourly
           WHERE hour >= NOW() - INTERVAL '7 days'"""
    )

    def _ratio(val: int, avg: float | None) -> float:
        if avg is None or avg < 0.5:
            # No baseline yet — any activity is mildly elevated
            return 1.5 if val > 0 else 1.0
        return val / avg

    avg_flag = float(averages["avg_flag"]) if averages["avg_flag"] is not None else None
    avg_ban = float(averages["avg_ban"]) if averages["avg_ban"] is not None else None
    flag_ratio = _ratio(current["flag_count"], avg_flag)
    ban_ratio = _ratio(current["ban_count"], avg_ban)
    return (flag_ratio + ban_ratio) / 2.0


# ─── Mode transitions ─────────────────────────────────────────────────────────

async def _enter_attack_mode(pool: asyncpg.Pool, threat_index: float | None) -> None:
    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'attack', track_a_paused = FALSE, paused_at = NULL,
               mode_entered_at = $1, threat_signal_index = $2, last_checked_at = $1
           WHERE id = 1""",
        now, threat_index,
    )
    await pool.execute(
        """INSERT INTO audit_log (action, severity, metadata)
           VALUES ('circuit_breaker_attack_mode', 'security_event', $1)""",
        {"threat_signal_index": threat_index, "mode": "attack"},
    )
    logger.warning(
        "circuit_breaker: ATTACK MODE entered (threat_signal_index=%.2f)", threat_index or 0
    )


async def _enter_conservative_mode(pool: asyncpg.Pool, threat_index: float) -> None:
    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'conservative', track_a_paused = TRUE, paused_at = $1,
               mode_entered_at = $1, threat_signal_index = $2, last_checked_at = $1
           WHERE id = 1""",
        now, threat_index,
    )
    await pool.execute(
        """INSERT INTO audit_log (action, severity, metadata)
           VALUES ('circuit_breaker_conservative_mode', 'security_event', $1)""",
        {"threat_signal_index": threat_index, "track_a_paused": True},
    )
    logger.warning(
        "circuit_breaker: CONSERVATIVE MODE — Track A paused (threat_signal_index=%.2f)", threat_index
    )


async def _reset_to_normal(pool: asyncpg.Pool, source: str = "auto_expire") -> None:
    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE circuit_breaker_state
           SET mode = 'normal', track_a_paused = FALSE, paused_at = NULL,
               mode_entered_at = $1, threat_signal_index = NULL, last_checked_at = $1
           WHERE id = 1""",
        now,
    )
    logger.info("circuit_breaker: reset to NORMAL (source=%s)", source)


async def reset_track_a(pool: asyncpg.Pool, source: str, threat_index: float) -> bool:
    """
    Escalation AI reset of Track A.
    All three conditions from Principle 7 must hold:
    1. threat_signal_index ≥ 1.5 — confirmed real attack
    2. Full audit_log entry written
    3. Called with Telegram-alert intent (caller's responsibility)
    Returns True if reset was performed.
    """
    if threat_index < THREAT_INDEX_ATTACK:
        logger.warning(
            "circuit_breaker: Track A reset denied — index %.2f < 1.5 (source=%s)",
            threat_index, source,
        )
        return False

    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE circuit_breaker_state
           SET track_a_paused = FALSE, paused_at = NULL, last_checked_at = $1
           WHERE id = 1""",
        now,
    )
    await pool.execute(
        """INSERT INTO audit_log (action, severity, metadata)
           VALUES ('circuit_breaker_track_a_reset', 'security_event', $1)""",
        {"source": source, "threat_signal_index": threat_index},
    )
    logger.info(
        "circuit_breaker: Track A reset by %s (threat_signal_index=%.2f)", source, threat_index
    )
    return True


# ─── Hourly stats snapshot ────────────────────────────────────────────────────

async def record_hourly_stats(pool: asyncpg.Pool) -> None:
    """Upsert current-hour stats so rolling averages stay current."""
    await pool.execute(
        """INSERT INTO circuit_stats_hourly (hour, flag_count, ban_count, new_agent_count)
           VALUES (
               DATE_TRUNC('hour', NOW()),
               (SELECT COUNT(*) FROM moderation_queue
                 WHERE flagged_at >= DATE_TRUNC('hour', NOW())),
               (SELECT COUNT(*) FROM bans
                 WHERE created_at >= DATE_TRUNC('hour', NOW())),
               (SELECT COUNT(*) FROM agents
                 WHERE created_at >= DATE_TRUNC('hour', NOW()))
           )
           ON CONFLICT (hour) DO UPDATE SET
               flag_count      = EXCLUDED.flag_count,
               ban_count       = EXCLUDED.ban_count,
               new_agent_count = EXCLUDED.new_agent_count"""
    )


# ─── Correlation gate ─────────────────────────────────────────────────────────

async def check_thresholds(pool: asyncpg.Pool) -> None:
    """
    Main gate — run by background worker on each interval.
    1. Snapshots current-hour stats for rolling avg computation.
    2. Handles mode auto-expiry (Attack Mode → normal after 2h;
       conservative → normal after 24h Track A pause).
    3. Evaluates anomaly thresholds and runs the correlation gate
       (high action rate + elevated threat signals → Attack Mode;
        high action rate + normal signals → Conservative Mode).
    """
    await record_hourly_stats(pool)
    state = await get_state(pool)
    now = datetime.now(timezone.utc)

    def _hours_since(ts: datetime) -> float:
        return (now - ts).total_seconds() / 3600.0

    # Auto-expire Attack Mode after 2h
    if state.mode == "attack":
        if _hours_since(state.mode_entered_at) >= ATTACK_MODE_DURATION_HOURS:
            await _reset_to_normal(pool, source="attack_mode_expired")
        return  # remain in attack mode until expiry

    # Auto-resume Track A after 24h conservative mode
    if state.mode == "conservative" and state.track_a_paused and state.paused_at:
        if _hours_since(state.paused_at) >= TRACK_A_AUTO_RESUME_HOURS:
            await _reset_to_normal(pool, source="conservative_24h_auto_resume")
            logger.info("circuit_breaker: Track A auto-resumed after 24h")
        return

    # Fetch current-hour rates
    current = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM bans WHERE created_at > NOW() - INTERVAL '1 hour') AS ban_count,
               (SELECT COUNT(*) FROM moderation_queue WHERE flagged_at > NOW() - INTERVAL '1 hour') AS flag_count,
               (SELECT COUNT(*) FROM agents WHERE created_at > NOW() - INTERVAL '1 hour') AS new_agent_count"""
    )
    bans = current["ban_count"] or 0
    flags = current["flag_count"] or 0
    new_agents = current["new_agent_count"] or 0

    # Threshold 1: ban rate > 20% → instant Attack Mode (no gate needed)
    if new_agents > 0 and (bans / new_agents) > BAN_RATE_ATTACK_THRESHOLD:
        threat_index = await compute_threat_signal_index(pool)
        await _enter_attack_mode(pool, threat_index)
        return

    # Threshold 2: ban rate 5–20% → correlation gate
    if new_agents > 0 and (bans / new_agents) > BAN_RATE_ANOMALY_THRESHOLD:
        threat_index = await compute_threat_signal_index(pool)
        if threat_index >= THREAT_INDEX_ATTACK:
            await _enter_attack_mode(pool, threat_index)
        else:
            await _enter_conservative_mode(pool, threat_index)
        return

    # Threshold 3: flag rate > 3× rolling average → correlation gate
    rolling = await pool.fetchrow(
        """SELECT AVG(flag_count) AS avg_flag FROM circuit_stats_hourly
           WHERE hour >= NOW() - INTERVAL '7 days'
             AND hour < DATE_TRUNC('hour', NOW())"""
    )
    avg_flag = rolling["avg_flag"] or 0.0
    if avg_flag > 0 and flags > FLAG_RATE_SPIKE_MULTIPLIER * avg_flag:
        threat_index = await compute_threat_signal_index(pool)
        if threat_index >= THREAT_INDEX_ATTACK:
            await _enter_attack_mode(pool, threat_index)
        else:
            await _enter_conservative_mode(pool, threat_index)
        return

    # No anomaly — touch last_checked_at
    await pool.execute(
        "UPDATE circuit_breaker_state SET last_checked_at = NOW() WHERE id = 1"
    )


# ─── Background worker ────────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None


async def _worker(pool: asyncpg.Pool, interval: int) -> None:
    while True:
        try:
            await check_thresholds(pool)
        except Exception:
            logger.exception("circuit_breaker: worker error")
        await asyncio.sleep(interval)


async def start_circuit_breaker_worker(pool: asyncpg.Pool, interval: int = 300) -> None:
    global _worker_task
    _worker_task = asyncio.create_task(_worker(pool, interval))


async def stop_circuit_breaker_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
