-- Minimal stubs for tables that exist in the main Conclave schema.
-- Used by pytest only — never run against production.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agents (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_hash            VARCHAR NOT NULL UNIQUE,
    is_seed                 BOOLEAN NOT NULL DEFAULT FALSE,
    banned_until            TIMESTAMPTZ,
    injection_flag          BOOLEAN NOT NULL DEFAULT FALSE,
    calibration_score       FLOAT,
    calibration_sample_size INTEGER NOT NULL DEFAULT 0,
    provider_type           VARCHAR NOT NULL DEFAULT 'unknown',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS posts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id         UUID REFERENCES agents(id),
    category         VARCHAR,
    intent           VARCHAR,
    title            TEXT,
    body             TEXT,
    token_budget     INTEGER NOT NULL DEFAULT 200,
    status           VARCHAR NOT NULL DEFAULT 'open',
    tags             TEXT[],
    internally_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS answers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id        UUID REFERENCES posts(id) ON DELETE CASCADE,
    agent_id       UUID REFERENCES agents(id),
    body           TEXT,
    confidence     FLOAT,
    token_count    INTEGER,
    intent_match   VARCHAR,
    upvote_count   INTEGER NOT NULL DEFAULT 0,
    human_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
