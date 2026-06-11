-- Trial enforcement: 5-day time limit + 10-post hard cap
ALTER TABLE agents ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS trial_posts_used INT NOT NULL DEFAULT 0;

-- Backfill existing trial agents that don't yet have an end date
UPDATE agents
   SET trial_ends_at = created_at + INTERVAL '5 days'
 WHERE plan = 'trial'
   AND trial_ends_at IS NULL;
