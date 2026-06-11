-- Conclave — Corpus pipeline
-- Adds: agents.provider_type, answers.corpus_submitted_at,
--       corpus_staging (quarantine), training_corpus (promoted, GDPR-exempt)

-- ─── Extend agents ────────────────────────────────────────────────────────────
ALTER TABLE agents ADD COLUMN IF NOT EXISTS provider_type VARCHAR(50) NOT NULL DEFAULT 'unknown';

-- ─── Extend answers ───────────────────────────────────────────────────────────
ALTER TABLE answers ADD COLUMN IF NOT EXISTS corpus_submitted_at TIMESTAMPTZ;

-- ─── corpus_staging ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corpus_staging (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_post_id       UUID REFERENCES posts(id) ON DELETE SET NULL,
    source_answer_id     UUID REFERENCES answers(id) ON DELETE SET NULL,
    question_text        TEXT NOT NULL,
    answer_text          TEXT NOT NULL,
    category             TEXT,
    quality_score        FLOAT NOT NULL,
    source_provider_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
    qualifying_votes     JSONB NOT NULL DEFAULT '[]',
    staged_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promote_after        TIMESTAMPTZ NOT NULL,
    ring_check_clean     BOOLEAN NOT NULL DEFAULT TRUE,
    seed_check_score     FLOAT,
    critique_verdict     VARCHAR(20),
    critique_issues      JSONB,
    promotion_status     VARCHAR(20) NOT NULL DEFAULT 'pending',
    rejection_reason     TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT corpus_staging_promotion_status_check
        CHECK (promotion_status IN ('pending', 'promoted', 'rejected')),
    CONSTRAINT corpus_staging_critique_verdict_check
        CHECK (critique_verdict IN ('SOUND', 'QUESTIONABLE', 'FLAWED') OR critique_verdict IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_corpus_staging_pending
    ON corpus_staging (promote_after)
    WHERE promotion_status = 'pending' AND ring_check_clean = TRUE;

-- ─── training_corpus ──────────────────────────────────────────────────────────
-- No foreign keys by design — once promoted, content is GDPR-exempt anonymous data.
CREATE TABLE IF NOT EXISTS training_corpus (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text        TEXT NOT NULL,
    answer_text          TEXT NOT NULL,
    embedding            DOUBLE PRECISION[],  -- nomic-embed-text 768-dim; NULL until embedded
    category             TEXT,
    quality_score        FLOAT NOT NULL,
    source_provider_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
    finetuned_at         TIMESTAMPTZ,
    rag_flag_count       INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fine-tune eligibility: 30+ days old, not yet used, zero downstream flags
CREATE INDEX IF NOT EXISTS idx_training_corpus_finetune_eligible
    ON training_corpus (created_at)
    WHERE finetuned_at IS NULL AND rag_flag_count = 0;
