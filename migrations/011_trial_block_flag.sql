-- Persist trial_posting_blocked in the circuit_breaker_state singleton
-- so restarts under attack don't reset the kill switch.
ALTER TABLE circuit_breaker_state
    ADD COLUMN IF NOT EXISTS trial_posting_blocked BOOLEAN NOT NULL DEFAULT FALSE;
