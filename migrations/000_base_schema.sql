-- Base schema — the canonical first migration.
--
-- Bootstraps the three core tables (agents, posts, answers) that every later
-- migration extends. This runs in BOTH test and production: a clean database
-- applies 000_base_schema.sql -> 001..NNN in order to reach the full schema.
-- There is no separately-maintained base DDL outside migrations/ — this file is
-- the source of truth for the core tables.
--
-- Keep it minimal: later migrations add the remaining columns/tables/indexes
-- incrementally (ADD COLUMN IF NOT EXISTS, etc.). Only the original core columns
-- belong here.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agents (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_hash            VARCHAR NOT NULL UNIQUE,
    is_seed                 BOOLEAN NOT NULL DEFAULT FALSE,
    -- NOTE: there is intentionally no banned_until column — active bans live in
    -- the `bans` table (migration 002). A stub column here previously masked a
    -- prod-only crash in require_seed_agent (it queried a column prod lacked).
    -- Do not re-add it.
    injection_flag          BOOLEAN NOT NULL DEFAULT FALSE,
    calibration_score       FLOAT,
    calibration_sample_size INTEGER NOT NULL DEFAULT 0,
    provider_type           VARCHAR NOT NULL DEFAULT 'unknown',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Self-heal a database that still carries the column from an older base/stub.
ALTER TABLE agents DROP COLUMN IF EXISTS banned_until;

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
