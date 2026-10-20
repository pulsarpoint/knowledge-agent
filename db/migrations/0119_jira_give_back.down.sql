BEGIN;
SET search_path = ka, public;

DROP INDEX notifications_give_back_due;
ALTER TABLE notifications
    DROP COLUMN give_back_key,
    DROP COLUMN give_back_name,
    DROP COLUMN give_back_status,
    DROP COLUMN give_back_error,
    DROP COLUMN give_back_attempts,
    DROP COLUMN give_back_next_attempt_at;
ALTER TABLE event_handlers DROP COLUMN give_back;

COMMIT;
