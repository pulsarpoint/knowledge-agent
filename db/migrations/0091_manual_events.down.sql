BEGIN;
SET search_path = ka, public;

-- Manual events are deleted (their handler runs go with them; agent tasks and notifications keep their rows).
DELETE FROM events WHERE source = 'manual';
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler'));

COMMIT;
