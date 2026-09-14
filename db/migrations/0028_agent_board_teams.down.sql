BEGIN;
DROP TABLE ka.agent_board_members;
DROP INDEX ka.agent_board_tasks_parent;
ALTER TABLE ka.agent_board_tasks DROP COLUMN parent_id,DROP COLUMN phase,DROP COLUMN plan,DROP COLUMN input_context;
COMMIT;
