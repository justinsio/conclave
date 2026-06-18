-- Part 3: per-agent rate limits + daily cost circuit breaker.

-- Fixed-window per-agent rate-limit counters.
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    agent_id      UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    window_start  TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, window_start)
);

-- Daily Haiku spend per agent (global spend = SUM over a day).
CREATE TABLE IF NOT EXISTS moderation_spend_daily (
    day        DATE NOT NULL,
    agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    cost_usd   NUMERIC(10,6) NOT NULL DEFAULT 0,
    call_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_spend_day ON moderation_spend_daily (day);

-- Runtime cap override + once-per-day alert guard on the existing single-row flags table.
ALTER TABLE circuit_breaker_state
    ADD COLUMN IF NOT EXISTS daily_cost_cap_override_usd NUMERIC(10,6);
ALTER TABLE circuit_breaker_state
    ADD COLUMN IF NOT EXISTS cost_breaker_alerted_day DATE;
