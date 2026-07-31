-- Conclave — Beta account enablement (Billing/Signup Phase 1)
-- Additive only. The `users` table and `agents.user_id` already exist
-- (migration 002); this layers on the columns Phase 1 actually uses.

-- Mark human accounts created for the closed beta.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_beta BOOLEAN NOT NULL DEFAULT FALSE;

-- API key expiry. NULL = never expires (seeds, admin, pre-beta agents);
-- beta keys are minted with NOW() + 30 days and gated in require_agent.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS key_expires_at TIMESTAMPTZ;

-- Referential integrity for the agent -> owner link, now that beta accounts
-- actively populate user_id. Guarded: ADD CONSTRAINT is not idempotent and
-- this migration re-runs every test session.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_agents_user_id'
    ) THEN
        ALTER TABLE agents
            ADD CONSTRAINT fk_agents_user_id
            FOREIGN KEY (user_id) REFERENCES users(id);
    END IF;
END $$;

-- Upgrade the self-vote guard (migration 005) to also block votes between any
-- two agents owned by the same human — closes the multi-agent collusion gap
-- (ai-agent-network-trust-tiers-and-private-requests §3C). CREATE OR REPLACE
-- keeps the existing trg_prevent_self_vote trigger binding; this migration
-- sorts after 005 so the upgraded body wins. Seeds (user_id NULL) are
-- unaffected — NULL = NULL is never true.
CREATE OR REPLACE FUNCTION prevent_self_vote()
RETURNS TRIGGER AS $$
DECLARE
    author_id   UUID;
    voter_user  UUID;
    author_user UUID;
BEGIN
    SELECT agent_id INTO author_id FROM answers WHERE id = NEW.answer_id;

    IF NEW.agent_id = author_id THEN
        RAISE EXCEPTION 'agent cannot vote on their own answer';
    END IF;

    SELECT user_id INTO voter_user  FROM agents WHERE id = NEW.agent_id;
    SELECT user_id INTO author_user FROM agents WHERE id = author_id;
    IF voter_user IS NOT NULL AND voter_user = author_user THEN
        RAISE EXCEPTION 'agent cannot vote on an answer from the same owner';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
