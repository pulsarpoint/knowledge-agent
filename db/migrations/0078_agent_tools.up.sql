BEGIN;
SET search_path = ka, public;

-- An agent's tools: each switched on or off, with optional usage instructions that replace the built-in text.
-- Catalog tools without a row are off; MCP tools without a row are on with the server's description.
CREATE TABLE agent_tools (
    agent_id         text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool             text NOT NULL CHECK (char_length(tool) BETWEEN 1 AND 300),
        -- catalog name ('read_file'), 'mcp:<mcp_server_id>:<tool name>' or, later, 'ask_agent:<agent id>'
    mode             text NOT NULL DEFAULT 'on' CHECK (mode IN ('off','on','per_run')),
        -- per_run: off unless a caller switches it on for one run (a later step)
    usage            text CHECK (usage IS NULL OR char_length(usage) <= 8000),
        -- NULL: the built-in usage instructions (catalog) or the MCP server's description
    subagent_allowed boolean NOT NULL DEFAULT true,
        -- run_subagent may give this tool to a subagent (a later step)
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, tool),
    CONSTRAINT subagents_do_not_spawn CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'ask_agent:%'))
);
COMMENT ON TABLE agent_tools IS 'The tools of each native agent and their usage instructions.';

-- Every native agent keeps the tools its capabilities gave it before tools were chosen (knowledge_admin.tools.catalog
-- implied_tools). code_graph is still offered only when a selected code project has a generated graph.
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool
FROM agents a
CROSS JOIN LATERAL (VALUES
    ('list_files',         a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('read_file',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('search_files',       a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('shell_run',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('code_search',        a.repositories_enabled),
    ('ast_grep',           a.repositories_enabled),
    ('code_graph',         a.repositories_enabled),
    ('search_documents',   a.documents_enabled),
    ('read_document',      a.documents_enabled),
    ('report_not_covered', a.documents_enabled AND a.documents_grounded),
    ('ssh_run',            a.ssh_enabled)
) AS t(tool, offered)
WHERE a.runtime = 'openai' AND t.offered;

COMMIT;
