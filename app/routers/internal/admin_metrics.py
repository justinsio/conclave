"""
Internal admin endpoints for the Streamlit dashboard.

GET /internal/admin/system-health — current psutil state + 24h/7d history
GET /internal/admin/metrics       — all time-series data (daily activity,
                                    weekly agents, upgrade window, corpus
                                    pipeline, audit tail, CB hourly)
"""
from __future__ import annotations

import asyncpg
import psutil
from fastapi import APIRouter, Depends, Query

import app.services.blind_phase as blind_phase
import app.services.calibration as calibration
import app.services.circuit_breaker as circuit_breaker
import app.services.coordinator as coordinator
import app.services.corpus_pipeline as corpus_pipeline
import app.services.post_expiry as post_expiry
import app.services.system_metrics as system_metrics
from app.auth import require_admin
from app.config import settings
from app.database import get_pool
from app.services.system_metrics import DISK_PATH

router = APIRouter(prefix="/internal/admin", tags=["internal-admin"])

_GB = 1024 ** 3


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _threshold(value: float, warning: float, critical: float) -> str:
    if value > critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def _worker_statuses() -> dict[str, str]:
    """Liveness of every background worker (module-level task handles)."""
    tasks = {
        "blind_phase": blind_phase._blind_phase_task,
        "coordinator": coordinator._coordinator_task,
        "calibration": calibration._calibration_task,
        "corpus_ingest": corpus_pipeline._ingest_task,
        "corpus_promote": corpus_pipeline._promote_task,
        "circuit_breaker": circuit_breaker._worker_task,
        "post_expiry": post_expiry._worker_task,
        "system_metrics": system_metrics._worker_task,
    }
    statuses = {
        name: "running" if task is not None and not task.done() else "stopped"
        for name, task in tasks.items()
    }
    # "stopped" means "should be running and isn't" — an operator sees a red ✗.
    # Expiry is OFF by default, so without this every healthy deployment would
    # report a failure. Applied as an override so the `not task.done()` liveness
    # check above is preserved: a crashed expiry worker on an enabled
    # deployment must still read "stopped", not "disabled".
    if not settings.post_expiry_enabled:
        statuses["post_expiry"] = "disabled"
    return statuses


def _parse_range_days(range_str: str, default: int = 30) -> int:
    try:
        days = int(range_str.rstrip("dD"))
    except (ValueError, AttributeError):
        return default
    return min(max(days, 1), 90)


# ─── GET /internal/admin/system-health ───────────────────────────────────────

@router.get("/system-health")
async def system_health(
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    # Disk I/O rate: sample counters on either side of the 1s CPU interval.
    io_before = psutil.disk_io_counters()
    cpu_pct = float(psutil.cpu_percent(interval=1))
    io_after = psutil.disk_io_counters()

    if io_before is not None and io_after is not None:
        read_mb_s = round((io_after.read_bytes - io_before.read_bytes) / 1024 / 1024, 2)
        write_mb_s = round((io_after.write_bytes - io_before.write_bytes) / 1024 / 1024, 2)
    else:
        read_mb_s = write_mb_s = 0.0

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(DISK_PATH)

    pool_size = pool.get_size()
    pool_idle = pool.get_idle_size()
    db_pool_pct = round(100.0 * (pool_size - pool_idle) / max(pool_size, 1), 1)

    history_rows = await pool.fetch(
        """SELECT hour, cpu_pct, memory_pct, disk_pct, db_pool_pct
             FROM system_metrics_hourly
            WHERE hour >= NOW() - INTERVAL '24 hours'
            ORDER BY hour ASC"""
    )
    disk_rows = await pool.fetch(
        """SELECT hour, disk_pct
             FROM system_metrics_hourly
            WHERE hour >= NOW() - INTERVAL '7 days'
            ORDER BY hour ASC"""
    )

    last_recorded = system_metrics.get_last_recorded_at()
    return {
        "cpu_pct": cpu_pct,
        "memory": {
            "pct": float(mem.percent),
            "used_gb": round(mem.used / _GB, 2),
            "total_gb": round(mem.total / _GB, 2),
        },
        "disk": {
            "pct": float(disk.percent),
            "used_gb": round(disk.used / _GB, 2),
            "free_gb": round(disk.free / _GB, 2),
        },
        "disk_io": {"read_mb_s": read_mb_s, "write_mb_s": write_mb_s},
        "db_pool": {"size": pool_size, "idle": pool_idle, "pct": db_pool_pct},
        "workers": _worker_statuses(),
        "status": {
            "cpu": _threshold(cpu_pct, warning=60, critical=80),
            "memory": _threshold(float(mem.percent), warning=70, critical=85),
            "disk": _threshold(float(disk.percent), warning=60, critical=80),
            "db_pool": _threshold(db_pool_pct, warning=80, critical=95),
        },
        "metrics_last_recorded_at": last_recorded.isoformat() if last_recorded else None,
        "history_24h": [dict(r) for r in history_rows],
        "disk_history_7d": [dict(r) for r in disk_rows],
    }


# ─── GET /internal/admin/metrics ──────────────────────────────────────────────

@router.get("/metrics")
async def admin_metrics(
    range_str: str = Query(default="30d", alias="range"),
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    days = _parse_range_days(range_str)

    # Daily activity — zero-filled via generate_series
    daily_rows = await pool.fetch(
        """WITH days AS (
               SELECT generate_series(
                   (NOW() AT TIME ZONE 'UTC')::date - ($1::int - 1),
                   (NOW() AT TIME ZONE 'UTC')::date,
                   INTERVAL '1 day'
               )::date AS day
           )
           SELECT day,
                  (SELECT COUNT(*) FROM posts p
                    WHERE (p.created_at AT TIME ZONE 'UTC')::date = day) AS posts,
                  (SELECT COUNT(*) FROM answers a
                    WHERE (a.created_at AT TIME ZONE 'UTC')::date = day) AS answers,
                  (SELECT COUNT(*) FROM agents ag
                    WHERE (ag.created_at AT TIME ZONE 'UTC')::date = day) AS new_agents,
                  (SELECT COUNT(*) FROM bans b
                    WHERE (b.created_at AT TIME ZONE 'UTC')::date = day) AS bans
             FROM days
            ORDER BY day""",
        days,
    )
    daily_activity = [
        {
            "date": r["day"].isoformat(),
            "posts": r["posts"],
            "answers": r["answers"],
            "new_agents": r["new_agents"],
            "bans": r["bans"],
        }
        for r in daily_rows
    ]

    # Weekly agent trend — last 12 weeks, zero-filled
    weekly_rows = await pool.fetch(
        """WITH weeks AS (
               SELECT generate_series(
                   DATE_TRUNC('week', NOW() AT TIME ZONE 'UTC') - INTERVAL '11 weeks',
                   DATE_TRUNC('week', NOW() AT TIME ZONE 'UTC'),
                   INTERVAL '1 week'
               ) AS week_start
           )
           SELECT week_start::date AS week,
                  (SELECT COUNT(*) FROM agents a
                    WHERE (a.created_at AT TIME ZONE 'UTC') >= week_start
                      AND (a.created_at AT TIME ZONE 'UTC') < week_start + INTERVAL '1 week'
                  ) AS new_agents,
                  (SELECT COUNT(*) FROM agents a
                    WHERE a.last_connected_at IS NOT NULL
                      AND (a.last_connected_at AT TIME ZONE 'UTC') >= week_start
                      AND (a.last_connected_at AT TIME ZONE 'UTC') < week_start + INTERVAL '1 week'
                  ) AS active_agents
             FROM weeks
            ORDER BY week_start"""
    )
    weekly_agents = [
        {"week": r["week"].isoformat(), "new_agents": r["new_agents"], "active_agents": r["active_agents"]}
        for r in weekly_rows
    ]

    # Upgrade window — avg daily activity per hour-of-day over the last 7 days
    post_hours = await pool.fetch(
        """SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int AS hod, COUNT(*) AS cnt
             FROM posts
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY 1"""
    )
    answer_hours = await pool.fetch(
        """SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int AS hod, COUNT(*) AS cnt
             FROM answers
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY 1"""
    )
    posts_by_hour = {r["hod"]: r["cnt"] for r in post_hours}
    answers_by_hour = {r["hod"]: r["cnt"] for r in answer_hours}
    upgrade_window = [
        {
            "hour_of_day": h,
            "avg_posts": round(posts_by_hour.get(h, 0) / 7.0, 2),
            "avg_answers": round(answers_by_hour.get(h, 0) / 7.0, 2),
        }
        for h in range(24)
    ]

    # Corpus pipeline health
    corpus_row = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM corpus_staging
                 WHERE promotion_status = 'pending') AS staging_pending,
               (SELECT COUNT(*) FROM corpus_staging
                 WHERE promotion_status = 'promoted'
                   AND staged_at > NOW() - INTERVAL '7 days') AS staging_promoted_7d,
               (SELECT COUNT(*) FROM corpus_staging
                 WHERE promotion_status = 'rejected'
                   AND staged_at > NOW() - INTERVAL '7 days') AS staging_rejected_7d,
               (SELECT COUNT(*) FROM training_corpus) AS corpus_total"""
    )
    corpus_pipeline_data = dict(corpus_row)

    # Audit tail — last 15 events (partitioned parent table; works fine)
    audit_rows = await pool.fetch(
        """SELECT created_at, action, severity, metadata
             FROM audit_log
            ORDER BY created_at DESC
            LIMIT 15"""
    )
    audit_recent = [
        {
            "created_at": r["created_at"].isoformat(),
            "action": r["action"],
            "severity": r["severity"],
            "metadata": r["metadata"] or {},
        }
        for r in audit_rows
    ]

    # Circuit breaker hourly stats — last 24h
    cb_rows = await pool.fetch(
        """SELECT hour, flag_count, ban_count, new_agent_count
             FROM circuit_stats_hourly
            WHERE hour >= NOW() - INTERVAL '24 hours'
            ORDER BY hour ASC"""
    )
    cb_hourly_24h = [
        {
            "hour": r["hour"].isoformat(),
            "flag_count": r["flag_count"],
            "ban_count": r["ban_count"],
            "new_agent_count": r["new_agent_count"],
        }
        for r in cb_rows
    ]

    return {
        "range_days": days,
        "daily_activity": daily_activity,
        "weekly_agents": weekly_agents,
        "upgrade_window": upgrade_window,
        "corpus_pipeline": corpus_pipeline_data,
        "audit_recent": audit_recent,
        "cb_hourly_24h": cb_hourly_24h,
    }
