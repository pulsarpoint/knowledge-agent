BEGIN;
SET search_path = ka, public;

-- One-time schedules go (with their history); repeating schedules stay.
DELETE FROM schedules WHERE run_at IS NOT NULL;
ALTER TABLE schedules
    DROP CONSTRAINT schedules_run_at_emit,
    DROP CONSTRAINT schedules_one_timetable,
    DROP COLUMN run_at,
    ALTER COLUMN cron SET NOT NULL;

COMMIT;
