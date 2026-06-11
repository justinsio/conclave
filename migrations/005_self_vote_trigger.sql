-- DB-level self-vote prevention.
-- votes.py already checks this at the application layer; the trigger closes
-- the direct-DB-access bypass path — a single point of authz failure the
-- Fable5 review flagged (finding 2.8 / Q7).

CREATE OR REPLACE FUNCTION prevent_self_vote()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.agent_id = (SELECT agent_id FROM answers WHERE id = NEW.answer_id) THEN
        RAISE EXCEPTION 'agent cannot vote on their own answer';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_self_vote ON votes;
CREATE TRIGGER trg_prevent_self_vote
    BEFORE INSERT ON votes
    FOR EACH ROW EXECUTE FUNCTION prevent_self_vote();
