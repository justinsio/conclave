-- ─── moderation_log ───────────────────────────────────────────────────────────
-- Every moderation verdict (structural + gate). This is the labeled corpus that
-- trains the local model later (distillation Phase 2). Never PII-linked beyond agent_id.
CREATE TABLE IF NOT EXISTS moderation_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type   VARCHAR(20) NOT NULL,            -- 'post' | 'answer'
    target_id     UUID,                            -- null for structural rejects (no row persisted)
    agent_id      UUID REFERENCES agents(id),
    content_hash  VARCHAR(64) NOT NULL,            -- sha256 of evaluated text
    stage         VARCHAR(20) NOT NULL,            -- 'structural' | 'gate'
    decision      VARCHAR(20) NOT NULL,            -- 'PASS' | 'BLOCK' | 'ESCALATE'
    confidence    FLOAT,
    category      VARCHAR(30),                     -- safe|harmful|spam|injection_attempt|uncertain|null
    reason        TEXT,
    model         VARCHAR(50) NOT NULL,            -- 'claude-haiku-4-5' | 'structural'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_moderation_log_created  ON moderation_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_moderation_log_decision ON moderation_log (decision, created_at DESC);
