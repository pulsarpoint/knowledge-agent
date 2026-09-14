BEGIN;
ALTER TABLE ka.agent_board_tasks
    ADD COLUMN parent_id uuid REFERENCES ka.agent_board_tasks(id),
    ADD COLUMN phase text NOT NULL DEFAULT 'agent' CHECK (phase IN ('agent','planning','working','reviewing','done')),
    ADD COLUMN plan text NOT NULL DEFAULT '',
    ADD COLUMN input_context jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(input_context)='array');
CREATE INDEX agent_board_tasks_parent ON ka.agent_board_tasks(parent_id,created_at,id);
CREATE TABLE ka.agent_board_members (
    task_id uuid NOT NULL REFERENCES ka.agent_board_tasks(id),
    agent_id text NOT NULL REFERENCES ka.agents(id),
    PRIMARY KEY (task_id,agent_id)
);
COMMENT ON TABLE ka.agent_board_members IS 'Agents explicitly selected for delegation within one parent task.';
COMMIT;
