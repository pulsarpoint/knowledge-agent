BEGIN;
SET search_path = ka, public;

ALTER TABLE event_handler_runs DROP COLUMN agent_task_id;
DROP TABLE agent_tasks;
DROP TABLE notifications;

COMMIT;
