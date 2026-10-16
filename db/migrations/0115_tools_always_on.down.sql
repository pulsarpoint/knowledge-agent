BEGIN;
SET search_path = ka, public;

-- The table and the column come back empty, as they were before 0115 (0078, 0085, 0108); the rows are not restored.
CREATE TABLE agent_tools (
    agent_id         text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool             text NOT NULL CHECK (char_length(tool) BETWEEN 1 AND 300),
    mode             text NOT NULL DEFAULT 'on' CHECK (mode IN ('off','on','per_run')),
    usage            text CHECK (usage IS NULL OR char_length(usage) <= 8000),
    subagent_allowed boolean NOT NULL DEFAULT true,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, tool),
    CONSTRAINT subagents_do_not_spawn CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'expert:%'))
);
COMMENT ON TABLE agent_tools IS 'The tools of each native agent and their usage instructions.';
COMMENT ON COLUMN agent_tools.tool IS 'A catalog name (read_file), mcp:<mcp_server_id>:<tool name> or expert:<agent id>.';

ALTER TABLE tools ADD COLUMN needs text CHECK (needs IN ('files', 'code', 'code_graph', 'documents', 'grounded', 'ssh',
    'server_proposals', 'resource_proposals', 'editable_document', 'resources'));

COMMIT;
