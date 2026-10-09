BEGIN;
SET search_path = ka, public;

-- Events can also be made by hand (Events → New event), to test handlers. The other checks stay: only a handler's
-- event has a parent, a handler and a depth.
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual'));

COMMIT;
