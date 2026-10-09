BEGIN;
SET search_path = ka, public;

ALTER TABLE schedules DROP COLUMN params;

COMMIT;
