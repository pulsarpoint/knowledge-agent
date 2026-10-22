BEGIN;
SET search_path = ka, public;

-- 0124: one-time schedules: an Emit event schedule runs once at run_at instead of repeating on a cron timetable.
-- After it fires it is switched off (next_run_at null); the row and its history stay.
ALTER TABLE schedules
    ALTER COLUMN cron DROP NOT NULL,
    ADD COLUMN run_at timestamptz,
    ADD CONSTRAINT schedules_one_timetable CHECK ((cron IS NULL) <> (run_at IS NULL)),
    ADD CONSTRAINT schedules_run_at_emit CHECK (run_at IS NULL OR target_kind = 'event.emit');
COMMENT ON COLUMN schedules.run_at IS
    'A one-time schedule runs once at this time (cron is null); Emit event schedules only. Null: repeats on cron.';

COMMIT;
