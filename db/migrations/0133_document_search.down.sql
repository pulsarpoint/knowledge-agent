BEGIN;
SET search_path = ka, public;

-- Back to 0132: no search index; every agent's "Answer only from documents" switch comes back off.
DROP INDEX documents_search;
ALTER TABLE agents ADD COLUMN documents_grounded boolean NOT NULL DEFAULT false;

COMMIT;
