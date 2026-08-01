-- 020: Normalize training_corpus embeddings to unit length.
--
-- Query-time similarity moves from cosine (three passes: dot + two magnitudes)
-- to a plain dot product. That is only correct if every stored vector is unit
-- length, so rows written before this migration must be rescaled in place.
--
-- Idempotent: normalizing an already-unit vector is a no-op, so re-running is
-- safe. Rows with a NULL or zero-magnitude embedding are skipped - a zero
-- vector has no direction and dividing by its magnitude would error.
--
-- ORDERING HAZARD: apply_migrations.py records applied filenames and skips them
-- permanently, so this runs exactly ONCE, ever. If the app restarts onto the new
-- code before this runs, every similarity is wrong until it does; if this runs
-- while the old code is still writing, those rows stay un-normalized FOREVER,
-- because the migration will not run again. deploy/conclave.service therefore
-- carries an ExecStartPre that applies migrations before the app starts.
--
-- ASCII-only by convention - see the header of 019_knowledge_lifecycle.sql.

UPDATE training_corpus
SET embedding = (
    SELECT array_agg(x / magnitude ORDER BY ord)
    FROM (
        SELECT elem AS x, ord, sqrt(sum(elem * elem) OVER ()) AS magnitude
        FROM unnest(embedding) WITH ORDINALITY AS t(elem, ord)
    ) scaled
    WHERE magnitude > 0
)
WHERE embedding IS NOT NULL
  AND (SELECT sqrt(sum(e * e)) FROM unnest(embedding) AS e) > 0;
