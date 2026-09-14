ALTER TABLE ka.agent_board_tasks
    ADD COLUMN trace_id text CHECK (trace_id ~ '^[0-9a-f]{32}$'),
    ADD COLUMN span_id text CHECK (span_id ~ '^[0-9a-f]{16}$');
