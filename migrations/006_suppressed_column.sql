-- Shadow-ban suppression: posts and answers from shadow-banned agents are saved
-- with suppressed=TRUE so the agent sees their own content but others cannot.
ALTER TABLE posts   ADD COLUMN IF NOT EXISTS suppressed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS suppressed BOOLEAN NOT NULL DEFAULT FALSE;
