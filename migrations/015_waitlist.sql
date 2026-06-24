-- Conclave — public "notify me when live" waitlist (marketing-site capture)
-- Standalone, additive. Written by the public endpoint POST /v1/waitlist.
-- Launch-time email send (SES batch) is a separate, later concern.

CREATE TABLE IF NOT EXISTS waitlist (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       TEXT NOT NULL,                 -- as submitted (display)
    email_norm  TEXT NOT NULL UNIQUE,          -- lower(trim(email)) — dedup key
    ip_hash     TEXT,                          -- sha256(client ip): privacy + per-IP rate limit
    source      TEXT,                          -- optional capture source (e.g. 'landing')
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backs the per-IP hourly rate-limit count.
CREATE INDEX IF NOT EXISTS idx_waitlist_ip_recent ON waitlist (ip_hash, created_at);
