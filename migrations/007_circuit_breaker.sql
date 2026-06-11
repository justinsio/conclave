-- Kill Shot 1 defense: split circuit breaker (Track A / Track B) + rolling stats.
-- Track A (bulk ops) can pause when a confused-AI anomaly is detected.
-- Track B (high-confidence single-target ≥ 0.95) always runs.

-- ─── Circuit breaker state (single-row) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    mode                VARCHAR(20) NOT NULL DEFAULT 'normal',
    track_a_paused      BOOLEAN NOT NULL DEFAULT FALSE,
    paused_at           TIMESTAMPTZ,
    mode_entered_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    threat_signal_index FLOAT,
    last_checked_at     TIMESTAMPTZ,

    CONSTRAINT cb_single_row CHECK (id = 1),
    CONSTRAINT cb_valid_mode CHECK (mode IN ('normal', 'conservative', 'attack'))
);

INSERT INTO circuit_breaker_state (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ─── Hourly stats for rolling-average threat computation ─────────────────────
CREATE TABLE IF NOT EXISTS circuit_stats_hourly (
    hour            TIMESTAMPTZ NOT NULL,
    flag_count      INTEGER NOT NULL DEFAULT 0,
    ban_count       INTEGER NOT NULL DEFAULT 0,
    new_agent_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour)
);
