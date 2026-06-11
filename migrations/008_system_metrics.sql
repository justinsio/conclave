-- Conclave — System metrics for dashboard trending
-- Hourly psutil snapshots written by app/services/system_metrics.py.
-- Read by GET /internal/admin/system-health (history_24h + disk_history_7d).

CREATE TABLE IF NOT EXISTS system_metrics_hourly (
    hour         TIMESTAMPTZ NOT NULL PRIMARY KEY,
    cpu_pct      FLOAT NOT NULL,
    memory_pct   FLOAT NOT NULL,
    disk_pct     FLOAT NOT NULL,
    db_pool_pct  FLOAT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sysmetrics_hour ON system_metrics_hourly (hour DESC);
