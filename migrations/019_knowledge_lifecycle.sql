-- 019: Knowledge lifecycle - invalidation, provenance, and flag storage.
--
-- Before this, training_corpus had exactly one INSERT and no UPDATE or DELETE
-- anywhere: nothing could remove, correct, or invalidate knowledge once stored.
-- Everything built decided what got IN; nothing decided what came OUT.
--
-- ASCII-only by choice: tests/conftest.py and scripts/apply_migrations.py both
-- read migrations with read_text() and no encoding=, so they decode with the
-- locale codec (cp1252 on the author's box). Comment-only non-ASCII survives as
-- mojibake, but a non-ASCII string literal would corrupt silently.

-- ---- Invalidation (soft removal) -------------------------------------------
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_at     TIMESTAMPTZ;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_reason TEXT;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_by     VARCHAR(20);

-- Spec section 5 enumerates exactly three legal writers. corpus_staging sets the
-- precedent (migration 004 CHECKs both promotion_status and critique_verdict).
-- Plan 2.7b writes 'flag_threshold' and 'propagation'; without a constraint a
-- typo there is silently stored and silently un-queryable.
-- DO block because ADD CONSTRAINT has no IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'training_corpus_invalidated_by_check'
    ) THEN
        ALTER TABLE training_corpus
            ADD CONSTRAINT training_corpus_invalidated_by_check
            CHECK (invalidated_by IS NULL
                   OR invalidated_by IN ('operator', 'flag_threshold', 'propagation'));
    END IF;
END $$;

-- ---- Provenance ------------------------------------------------------------
-- Deliberately NO foreign keys. Posts expire; an FK would either block that
-- expiry or null the link out. A dangling UUID is an acceptable breadcrumb, and
-- with CORPUS_ANONYMIZE=false the entry holds the full original text anyway, so
-- the content survives regardless of whether the source row does.
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_post_id   UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_answer_id UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_agent_id  UUID;

-- NO BACKFILL. Provenance for pre-2.7a corpus entries is permanently
-- unrecoverable, and it is worth being exact about why.
--
-- run_promote INSERTs the training_corpus row and, in the SAME transaction, sets
-- corpus_staging.source_post_id = NULL and source_answer_id = NULL
-- (app/services/corpus_pipeline.py, the UPDATE corpus_staging after the INSERT).
-- A training_corpus row exists only because its staging row was promoted.
-- Therefore every staging row that could be joined has already had its
-- provenance nulled - for every corpus entry, always, regardless of
-- anonymization. An attempted backfill reports "UPDATE n" and writes NULL over
-- NULL, which is worse than no backfill because it looks like it worked.
--
-- Consequences, stated rather than papered over:
--   * Pre-2.7a entries keep NULL provenance forever. The 2.7b flag-author guard
--     treats those as "all flags count".
--   * The spec section 3b expiry exemption ("posts with a corpus descendant
--     never expire") does not protect the source posts of pre-2.7a entries.
-- Only entries promoted AFTER this migration carry provenance.

-- ---- Flag storage (populated by plan 2.7b; created here so one phase = one migration) ----
CREATE TABLE IF NOT EXISTS answer_flags (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id  UUID NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    agent_id   UUID NOT NULL REFERENCES agents(id)  ON DELETE CASCADE,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT answer_flags_one_per_agent UNIQUE (answer_id, agent_id)
);

-- corpus_flags.corpus_id CASCADEs so a purge takes its flags with it. The
-- "no foreign keys on training_corpus" rule concerns FKs pointing OUTWARD from
-- that table; inbound references are fine.
CREATE TABLE IF NOT EXISTS corpus_flags (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corpus_id  UUID NOT NULL REFERENCES training_corpus(id) ON DELETE CASCADE,
    agent_id   UUID NOT NULL REFERENCES agents(id)          ON DELETE CASCADE,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT corpus_flags_one_per_agent UNIQUE (corpus_id, agent_id)
);

-- ---- Indexes ---------------------------------------------------------------
-- DROP first. CREATE INDEX IF NOT EXISTS with the same name silently keeps the
-- OLD predicate, so the migration reports success while changing nothing.
DROP INDEX IF EXISTS idx_training_corpus_finetune_eligible;
CREATE INDEX idx_training_corpus_finetune_eligible
    ON training_corpus (created_at)
    WHERE finetuned_at IS NULL AND rag_flag_count = 0 AND invalidated_at IS NULL;

-- Narrows the retrieval scan to live, embedded rows. Be honest about the size of
-- that win: the seed retrieval path issues an unbounded SELECT with no ORDER BY
-- and no LIMIT, then scores every row in Python, so the (category) key
-- accelerates nothing here - only the partial predicate does any work. Kept
-- because 2.8 adds a category-filtered public retrieval path that will use it.
CREATE INDEX IF NOT EXISTS idx_training_corpus_active
    ON training_corpus (category)
    WHERE invalidated_at IS NULL AND embedding IS NOT NULL;
