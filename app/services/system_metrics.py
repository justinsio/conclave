"""
System metrics worker — hourly psutil snapshots for dashboard trending.

Writes one row per hour into system_metrics_hourly (upsert, idempotent).
The dashboard reads these via GET /internal/admin/system-health.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import asyncpg
import psutil

logger = logging.getLogger(__name__)

# psutil.disk_usage needs a platform-valid mount point ("/" fails on Windows).
DISK_PATH = "C:\\" if os.name == "nt" else "/"

_worker_task: asyncio.Task | None = None
_last_recorded_at: datetime | None = None


def get_last_recorded_at() -> datetime | None:
    """Exposed for the system-health endpoint."""
    return _last_recorded_at


async def record_system_metrics(pool: asyncpg.Pool) -> None:
    """Take one psutil snapshot and upsert the current hour's row."""
    global _last_recorded_at

    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cpu = float(psutil.cpu_percent(interval=1))
    memory = float(psutil.virtual_memory().percent)
    disk = float(psutil.disk_usage(DISK_PATH).percent)

    pool_size = pool.get_size()
    pool_idle = pool.get_idle_size()
    db_pct = round(100.0 * (pool_size - pool_idle) / max(pool_size, 1), 1)

    await pool.execute(
        """INSERT INTO system_metrics_hourly (hour, cpu_pct, memory_pct, disk_pct, db_pool_pct)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (hour) DO UPDATE SET
               cpu_pct     = EXCLUDED.cpu_pct,
               memory_pct  = EXCLUDED.memory_pct,
               disk_pct    = EXCLUDED.disk_pct,
               db_pool_pct = EXCLUDED.db_pool_pct""",
        hour, cpu, memory, disk, db_pct,
    )
    _last_recorded_at = datetime.now(timezone.utc)


# ─── Background worker ────────────────────────────────────────────────────────

async def _worker(pool: asyncpg.Pool, interval: int) -> None:
    while True:
        try:
            await record_system_metrics(pool)
        except Exception:
            logger.exception("system_metrics: worker error")
        await asyncio.sleep(interval)


async def start_system_metrics_worker(pool: asyncpg.Pool, interval: int = 3600) -> None:
    global _worker_task
    _worker_task = asyncio.create_task(_worker(pool, interval))


async def stop_system_metrics_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
