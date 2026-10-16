BEGIN;
SET search_path = ka, public;

-- A saved agent stores what it may use as one access object (references only: names, descriptions and paths are
-- loaded at run start) and its instructions on its own row. Each agent is converted from its per-type tables: a kind
-- switched off is left out, "all collections" / "all code projects" become every current collection / code project,
-- MCP servers are the agent-level ones (per-server overrides and per-agent SSH connections are not kept). The old
-- tables stay until 0114.
ALTER TABLE agents
    ADD COLUMN access jsonb NOT NULL DEFAULT '{"resources": [], "roles": {"content_editor": false}}',
    ADD COLUMN instructions text NOT NULL DEFAULT '' CHECK (char_length(instructions) <= 40000);

WITH refs AS (
    SELECT a.id AS agent_id, 1 AS rank, 'server' AS type, o.server_id::text AS id
    FROM agents a JOIN operations_servers o ON o.agent = a.id WHERE a.ssh_enabled
    UNION SELECT a.id, 2, 'service', s.service_id::text
    FROM agents a JOIN agent_services s ON s.agent_id = a.id WHERE a.services_enabled
    UNION SELECT a.id, 3, 'code_project', r.repository_id::text
    FROM agents a JOIN agent_repositories r ON r.agent_id = a.id WHERE a.repositories_enabled AND NOT a.all_repositories
    UNION SELECT a.id, 3, 'code_project', r.id::text
    FROM agents a JOIN repositories r ON r.archived_at IS NULL WHERE a.repositories_enabled AND a.all_repositories
    UNION SELECT a.id, 4, 'collection', c.collection_id::text
    FROM agents a JOIN agent_collections c ON c.agent_id = a.id WHERE a.documents_enabled AND NOT a.all_collections
    UNION SELECT a.id, 4, 'collection', c.id::text
    FROM agents a JOIN rag_collections c ON c.deleted_at IS NULL WHERE a.documents_enabled AND a.all_collections
    UNION SELECT a.id, 5, 'skill', s.skill_id::text
    FROM agents a JOIN skills_agents s ON s.agent_id = a.id WHERE a.skills_enabled
    UNION SELECT a.id, 6, 'mcp_server', m.mcp_server_id::text
    FROM agents a JOIN agent_configs g ON g.agent = a.id AND g.server_id IS NULL
    JOIN agent_config_mcp_servers m ON m.config_id = g.id WHERE a.mcp_enabled
)
UPDATE agents a SET access = jsonb_build_object('resources', r.resources, 'roles', '{"content_editor": false}'::jsonb)
FROM (
    SELECT agent_id, jsonb_agg(jsonb_build_object('type', type, 'id', id) ORDER BY rank, id) AS resources
    FROM refs GROUP BY agent_id
) r
WHERE r.agent_id = a.id;

UPDATE agents a SET instructions = g.instructions
FROM agent_configs g WHERE g.agent = a.id AND g.server_id IS NULL AND g.instructions IS NOT NULL;

COMMIT;
