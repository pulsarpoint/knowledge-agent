BEGIN;
SET search_path = ka, public;

-- Target settings, e.g. {"jql": ..., "limit": ...} for jira.pull (the pull's own query, separate from the manual fetch).
ALTER TABLE schedules ADD COLUMN params jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object');

COMMIT;
