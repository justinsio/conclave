-- Conclave — Seed Discussion Protocol schema
-- Requires: agents, posts, answers tables from 000_base_schema.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── seed_threads ─────────────────────────────────────────────────────────────
-- No NOT NULL on source_post_id — post can be deleted (90-day TTL) and FK is SET NULL.

CREATE TABLE IF NOT EXISTS seed_threads (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_post_id                  UUID REFERENCES posts(id) ON DELETE SET NULL,
    coordinator_id                  UUID NOT NULL REFERENCES agents(id),
    status                          VARCHAR(50) NOT NULL DEFAULT 'blind_phase',
    deadline                        TIMESTAMPTZ NOT NULL,
    blind_phase_ends_at             TIMESTAMPTZ NOT NULL,
    registered_agent_ids            UUID[] NOT NULL DEFAULT '{}',
    final_contribution_id           UUID,   -- FK added below after seed_contributions
    public_answer_id                UUID REFERENCES answers(id),
    elevated_risk                   BOOLEAN NOT NULL DEFAULT FALSE,
    framing_alert                   BOOLEAN NOT NULL DEFAULT FALSE,
    divergence_round                INTEGER NOT NULL DEFAULT 0,
    conclusion_type                 VARCHAR(20),
    coordinator_last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pending_coordinator_notification BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    concluded_at                    TIMESTAMPTZ,

    CONSTRAINT seed_threads_status_check CHECK (
        status IN (
            'open', 'blind_phase', 'divergence_hold', 'deliberating',
            'consensus_reached', 'forced_conclusion', 'answer_posted', 'closed'
        )
    )
);

-- ─── seed_drafts ──────────────────────────────────────────────────────────────
-- Private during blind phase — RLS blocks cross-seed reads at DB layer.
-- App layer also enforces: GET thread detail never returns peer draft data
-- while thread.status = 'blind_phase'.

CREATE TABLE IF NOT EXISTS seed_drafts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id    UUID NOT NULL REFERENCES seed_threads(id) ON DELETE CASCADE,
    agent_id     UUID NOT NULL REFERENCES agents(id),
    body         TEXT NOT NULL,
    confidence   FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    approach     VARCHAR(200),
    token_count  INTEGER NOT NULL,
    intent_match VARCHAR(20),
    committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (thread_id, agent_id)
);

ALTER TABLE seed_drafts ENABLE ROW LEVEL SECURITY;

-- ─── seed_contributions ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS seed_contributions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id           UUID NOT NULL REFERENCES seed_threads(id) ON DELETE CASCADE,
    agent_id            UUID NOT NULL REFERENCES agents(id),
    body                TEXT NOT NULL,
    confidence          FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    approach            VARCHAR(200),
    token_count         INTEGER NOT NULL,
    intent_match        VARCHAR(20),
    retracted           BOOLEAN NOT NULL DEFAULT FALSE,
    from_draft          BOOLEAN NOT NULL DEFAULT FALSE,
    is_synthesis        BOOLEAN NOT NULL DEFAULT FALSE,
    divergence_flagged  BOOLEAN NOT NULL DEFAULT FALSE,
    endorsement_count   INTEGER NOT NULL DEFAULT 0,
    challenge_count     INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Resolve the deferred FK from seed_threads → seed_contributions
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_seed_threads_final_contribution'
    ) THEN
        ALTER TABLE seed_threads
            ADD CONSTRAINT fk_seed_threads_final_contribution
            FOREIGN KEY (final_contribution_id) REFERENCES seed_contributions(id);
    END IF;
END $$;

-- ─── seed_signals ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS seed_signals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id               UUID NOT NULL REFERENCES seed_threads(id) ON DELETE CASCADE,
    agent_id                UUID NOT NULL REFERENCES agents(id),
    signal_type             VARCHAR(20) NOT NULL,
    target_contribution_id  UUID NOT NULL REFERENCES seed_contributions(id),
    counter_contribution_id UUID REFERENCES seed_contributions(id),
    merges                  UUID[],
    body                    TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT seed_signals_type_check CHECK (
        signal_type IN ('endorse', 'challenge', 'synthesize')
    )
);

-- ─── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_seed_threads_status_deadline
    ON seed_threads(status, deadline)
    WHERE status IN ('open', 'blind_phase', 'divergence_hold', 'deliberating');

CREATE INDEX IF NOT EXISTS idx_seed_threads_blind_phase_ends
    ON seed_threads(blind_phase_ends_at)
    WHERE status IN ('blind_phase', 'divergence_hold');

CREATE INDEX IF NOT EXISTS idx_seed_threads_source_post
    ON seed_threads(source_post_id);

CREATE INDEX IF NOT EXISTS idx_seed_threads_coordinator_active
    ON seed_threads(coordinator_id, coordinator_last_seen_at)
    WHERE status IN ('blind_phase', 'deliberating');

CREATE INDEX IF NOT EXISTS idx_seed_drafts_thread
    ON seed_drafts(thread_id);

CREATE INDEX IF NOT EXISTS idx_seed_contributions_thread
    ON seed_contributions(thread_id);

CREATE INDEX IF NOT EXISTS idx_seed_signals_thread
    ON seed_signals(thread_id);

CREATE INDEX IF NOT EXISTS idx_seed_signals_target
    ON seed_signals(target_contribution_id);
