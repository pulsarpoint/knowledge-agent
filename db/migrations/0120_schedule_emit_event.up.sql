BEGIN;
SET search_path = ka, public;

-- 0120: a schedule can emit an event. An event.emit schedule is its own target (target_id = its id); each firing
-- records one schedule.fired event (source schedule, source_id = the schedule) that handlers route to agents.
ALTER TABLE schedules
    DROP CONSTRAINT schedules_target_kind_check,
    ADD CONSTRAINT schedules_target_kind_check CHECK (target_kind IN ('jira.pull', 'event.emit'));
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual', 'memory', 'schedule'));
COMMENT ON COLUMN schedules.target_id IS 'The target''s row, e.g. jira_connections.id for jira.pull; the schedule''s own id for event.emit (no foreign key: targets live in different tables).';

COMMIT;
