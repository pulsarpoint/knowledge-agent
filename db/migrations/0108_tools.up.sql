BEGIN;
SET search_path = ka, public;

-- 0108: the tool attributes used to build agents (group, label, summary, needs, order), one row per built-in tool.
-- Filled and kept equal to services/admin-api/src/knowledge_admin/tools/tools.json by the admin-api start
-- (tools/fixture.sync_tools); the fixture is the only writer. agent_tools.tool is not a foreign key: it also names
-- MCP tools (mcp:<server>:<tool>).
CREATE TABLE ka.tools (
    name       text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    position   integer NOT NULL UNIQUE CHECK (position >= 0),
    tool_group text NOT NULL CHECK (tool_group IN ('code', 'documents', 'servers', 'inventory', 'agents')),
    label      text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
    summary    text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 300),
    needs      text CHECK (needs IN ('files', 'code', 'code_graph', 'documents', 'grounded', 'ssh',
                                     'server_proposals', 'resource_proposals', 'editable_document', 'resources'))
);

COMMIT;
