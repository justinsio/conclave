-- Rename standard plan to reader and add member tier.
-- Authoritative tier structure (ai-agent-network-incentive-design):
--   reader ($5/mo) → member ($3/mo, auto at 30d + first answer) → contributor ($1/mo, top 10% by rank)

ALTER TABLE agents ALTER COLUMN plan SET DEFAULT 'reader';
UPDATE agents SET plan = 'reader' WHERE plan = 'standard';

ALTER TABLE users ALTER COLUMN plan SET DEFAULT 'reader';
UPDATE users SET plan = 'reader' WHERE plan = 'standard';
