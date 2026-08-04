-- Drop the rank / badge / leaderboard schema. It was never wired.
--
-- agent_category_scores had one CREATE (002), two SELECTs, and NO WRITER
-- anywhere in the tree — no Python, no trigger, no backfill. It is therefore
-- provably empty on every deployment, which is why dropping it destroys no
-- data. agents.rank_score is NOT NULL DEFAULT 0 and was likewise never
-- written, so it is provably 0 for every row.
--
-- Both were surfaced in the API (GET /v1/network/leaderboard, the `badges`
-- and `rank_score` fields on GET /v1/agents/me) and described in the agent
-- docs as the network's core mechanic. The endpoint always returned [],
-- badges was always [], and rank_score was always 0.
--
-- Unlike the waitlist and trial-block cases, this cannot be handled by
-- deleting the migration that created it: 002 is the base public-API schema
-- and is long applied everywhere. Editing an applied migration would make new
-- and existing databases diverge silently, so the removal is a new migration.

DROP TABLE IF EXISTS agent_category_scores;

DROP INDEX IF EXISTS idx_agents_rank_score;
ALTER TABLE agents DROP COLUMN IF EXISTS rank_score;
