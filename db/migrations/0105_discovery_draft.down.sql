BEGIN;
SET search_path = ka, public;

-- Drafts made by accepting proposals stay as drafts; the proposals only forget which draft they made.
ALTER TABLE discovery_proposals DROP COLUMN draft_id;

COMMIT;
