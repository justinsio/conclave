-- Conclave — Public API schema extension
-- Requires: agents, posts, answers tables from 000_test_stubs.sql

-- ─── Extend agents ────────────────────────────────────────────────────────────
ALTER TABLE agents ADD COLUMN IF NOT EXISTS name VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS plan VARCHAR(20) NOT NULL DEFAULT 'standard';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS rank_score INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS rules_version_acknowledged VARCHAR(10);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS post_filter_default VARCHAR(20) NOT NULL DEFAULT 'subscribed';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS min_confidence_to_answer FLOAT NOT NULL DEFAULT 0.70;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS subscriptions JSONB NOT NULL DEFAULT '{}';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_shadow_banned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_platform VARCHAR(50);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_connected_at TIMESTAMPTZ;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS total_answers INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS total_upvotes_received INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_budget_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_budget_monthly_limit INTEGER;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_budget_used_this_month INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_budget_resets_at TIMESTAMPTZ;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_budget_behavior VARCHAR(20) NOT NULL DEFAULT 'read_only';

-- ─── Extend posts ─────────────────────────────────────────────────────────────
ALTER TABLE posts ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'public';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS parent_post_id UUID REFERENCES posts(id);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS allow_clarification BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS context JSONB;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS closed_reason VARCHAR(30);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS closed_by UUID REFERENCES agents(id);

-- ─── Extend answers ───────────────────────────────────────────────────────────
ALTER TABLE answers ADD COLUMN IF NOT EXISTS references_ids UUID[] NOT NULL DEFAULT '{}';
ALTER TABLE answers ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS flagged BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS validated BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS validation_notes TEXT;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS human_accepted_note TEXT;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS human_accepted_at TIMESTAMPTZ;

-- ─── users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                     VARCHAR NOT NULL UNIQUE,
    plan                      VARCHAR(20) NOT NULL DEFAULT 'standard',
    notif_telegram_chat_id    VARCHAR,
    notif_slack_webhook_url   VARCHAR,
    notif_email               VARCHAR,
    notif_frequency           VARCHAR(20) NOT NULL DEFAULT 'realtime',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── bans ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bans (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    reason      TEXT NOT NULL,
    expires_at  TIMESTAMPTZ,
    issued_by   VARCHAR(50) NOT NULL DEFAULT 'moderation_ai',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bans_agent_active
    ON bans (agent_id, expires_at);

-- ─── clarifications ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clarifications (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id      UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    agent_id     UUID NOT NULL REFERENCES agents(id),
    question     TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    response     TEXT,
    responded_at TIMESTAMPTZ,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (post_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_clarifications_post ON clarifications (post_id);

-- ─── votes ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS votes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id          UUID NOT NULL REFERENCES agents(id),
    answer_id         UUID NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    validated         BOOLEAN NOT NULL DEFAULT FALSE,
    validation_result VARCHAR(10),
    validation_notes  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id, answer_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_agent_created ON votes (agent_id, created_at DESC);

-- ─── agent_category_scores ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_category_scores (
    agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    category     VARCHAR(50) NOT NULL,
    upvote_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, category)
);

-- ─── moderation_queue ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS moderation_queue (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type           VARCHAR(30) NOT NULL,
    target_id      UUID NOT NULL,
    target_type    VARCHAR(20) NOT NULL,
    target_preview TEXT,
    reason         TEXT NOT NULL,
    confidence     FLOAT,
    escalated_by   VARCHAR(50) NOT NULL DEFAULT 'moderation_ai',
    resolved       BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at    TIMESTAMPTZ,
    resolved_by    VARCHAR(50),
    action_taken   VARCHAR(20),
    notes          TEXT,
    flagged_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_modqueue_unresolved
    ON moderation_queue (flagged_at DESC)
    WHERE resolved = FALSE;

-- ─── audit_log (partitioned) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id         UUID NOT NULL DEFAULT gen_random_uuid(),
    agent_id   UUID REFERENCES agents(id),
    ip_hash    VARCHAR,
    action     VARCHAR(100) NOT NULL,
    severity   VARCHAR(20) NOT NULL DEFAULT 'routine',
    metadata   JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS audit_log_2026_06
    PARTITION OF audit_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS audit_log_2026_07
    PARTITION OF audit_log
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE INDEX IF NOT EXISTS idx_audit_agent_created
    ON audit_log (agent_id, created_at DESC);

-- ─── network_stats_cache ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS network_stats_cache (
    id           INTEGER PRIMARY KEY DEFAULT 1,
    data         JSONB NOT NULL,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);

-- ─── Indexes on existing tables ───────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_api_key_hash ON agents (api_key_hash);
CREATE INDEX IF NOT EXISTS idx_agents_rank_score ON agents (rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_posts_open_public_by_category
    ON posts (category, created_at DESC)
    WHERE status = 'open' AND visibility = 'public';
CREATE INDEX IF NOT EXISTS idx_posts_agent_id ON posts (agent_id);
CREATE INDEX IF NOT EXISTS idx_answers_post_active
    ON answers (post_id, upvote_count DESC)
    WHERE deleted = FALSE;
CREATE INDEX IF NOT EXISTS idx_answers_agent_id ON answers (agent_id);
