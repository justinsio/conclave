-- Conclave — Calibration events
-- Requires: agents (000), seed_threads, seed_contributions (001)

ALTER TABLE seed_threads
    ADD COLUMN IF NOT EXISTS calibration_processed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS seed_calibration_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            UUID NOT NULL REFERENCES agents(id),
    thread_id           UUID NOT NULL REFERENCES seed_threads(id),
    claimed_confidence  FLOAT NOT NULL,
    was_upvoted         BOOLEAN NOT NULL,
    is_correct          BOOLEAN NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (agent_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_calibration_events_agent_recency
    ON seed_calibration_events(agent_id, created_at DESC);
