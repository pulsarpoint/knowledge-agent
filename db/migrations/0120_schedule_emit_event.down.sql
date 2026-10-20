BEGIN;
SET search_path = ka, public;

DELETE FROM events WHERE source = 'schedule';
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual', 'memory'));
DELETE FROM schedules WHERE target_kind = 'event.emit';
ALTER TABLE schedules
    DROP CONSTRAINT schedules_target_kind_check,
    ADD CONSTRAINT schedules_target_kind_check CHECK (target_kind IN ('jira.pull'));
COMMENT ON COLUMN schedules.target_id IS 'The target''s row, e.g. jira_connections.id for jira.pull (no foreign key: targets live in different tables).';

COMMIT;
