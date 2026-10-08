BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- An agent has one configuration. First, what runs today becomes the agent: each agent with an active configuration
-- version gets that version's configuration, as get_agent_config read it (versions saved before 0072/0075/0078: all
-- Claude Code tools, fixed resources, 0078's implied tools), including its description and its name (unless another
-- agent has that name). The agent type and enabled stay the agent's.
-- The agent's tables reference live resources: the version's frozen copies of resource details (model settings,
-- server host and port, skill texts, MCP URL and tools, collection paths, code folders, service details) are not
-- kept. Items deleted or archived since the version was saved are dropped, "all" selections include items created
-- since, and edits made since to shared resources apply. Each migrated agent gets an audit event holding the version.
-- Without exactly one application project (the loader refuses that too) agents keep their own configuration.
CREATE TEMPORARY TABLE active_versions ON COMMIT DROP AS
SELECT a.id AS agent_id, a.runtime, v.id AS version_id, v.label, v.config AS c,
    CASE WHEN (SELECT count(*) FROM projects WHERE archived_at IS NULL) = 1
        THEN (SELECT id FROM projects WHERE archived_at IS NULL) END AS project_id
FROM agents a JOIN agent_versions v ON v.id = a.active_config_version_id AND v.agent = a.id;
DELETE FROM active_versions WHERE project_id IS NULL;

INSERT INTO audit_events(project_id, action, resource_type, details)
SELECT project_id, 'agent.version.materialized', 'agent', jsonb_build_object('migration', 80, 'agent_id', agent_id,
    'version_id', version_id, 'label', label, 'config', c)
FROM active_versions;

UPDATE agents a SET
    name = v.c->>'name'
FROM active_versions v WHERE v.agent_id = a.id AND v.project_id IS NOT NULL
    AND char_length(btrim(coalesce(v.c->>'name', ''))) BETWEEN 1 AND 100 AND v.c->>'name' <> a.name
    AND NOT EXISTS (SELECT 1 FROM agents o WHERE o.name = v.c->>'name' AND o.id <> a.id)
    AND NOT EXISTS (SELECT 1 FROM active_versions w WHERE w.c->>'name' = v.c->>'name' AND w.agent_id <> v.agent_id);

UPDATE agents a SET
    description = coalesce(left(v.c->>'description', 4000), a.description),
    when_to_use = coalesce(v.c->>'when_to_use', a.when_to_use),
    model_id = (SELECT m.id FROM llm_models m WHERE m.id::text = v.c->'model'->>'id'),
    claude_code_tools = CASE WHEN v.c ? 'claude_code_tools'
        THEN ARRAY(SELECT jsonb_array_elements_text(v.c->'claude_code_tools')) ELSE '{Read,Grep,Glob,Bash}' END,
    resources = coalesce(v.c->>'resources', 'fixed'),
    system_prompt = coalesce(v.c->'system_prompt'->>'content', a.system_prompt),
    documents_enabled = coalesce((v.c->'documents'->>'enabled')::boolean, false),
    documents_grounded = coalesce((v.c->'documents'->>'grounded')::boolean, false),
    all_collections = coalesce((v.c->'documents'->>'all_collections')::boolean, false),
    repositories_enabled = coalesce((v.c->'code'->>'enabled')::boolean, false),
    all_repositories = coalesce((v.c->'code'->>'all_projects')::boolean, false),
    ssh_enabled = coalesce((v.c->'ssh'->>'enabled')::boolean, false),
    skills_enabled = coalesce((v.c->'skills'->>'enabled')::boolean, false),
    mcp_enabled = coalesce((v.c->'mcp'->>'enabled')::boolean, false),
    services_enabled = coalesce((v.c->'services'->>'enabled')::boolean, false),
    revision = a.revision + 1
FROM active_versions v WHERE v.agent_id = a.id;

DELETE FROM agent_collections s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_collections(agent_id, collection_id)
SELECT DISTINCT v.agent_id, k.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'documents'->'collections', '[]')) e
JOIN rag_collections k ON k.id::text = e->>'id' AND k.deleted_at IS NULL
WHERE NOT coalesce((v.c->'documents'->>'all_collections')::boolean, false);
DELETE FROM agent_repositories s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_repositories(agent_id, repository_id)
SELECT DISTINCT v.agent_id, r.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'code'->'projects', '[]')) e
JOIN repositories r ON r.id::text = e->>'id' AND r.archived_at IS NULL
WHERE NOT coalesce((v.c->'code'->>'all_projects')::boolean, false);
DELETE FROM agent_services s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO agent_services(agent_id, service_id)
SELECT DISTINCT v.agent_id, s.id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'services'->'items', '[]')) e
JOIN services s ON s.id::text = e->>'id';
DELETE FROM skills_agents s USING active_versions v WHERE s.agent_id = v.agent_id;
INSERT INTO skills_agents(agent_id, skill_id, project_id)
SELECT DISTINCT v.agent_id, k.id, k.project_id FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'skills'->'items', '[]')) e
JOIN agent_skills k ON k.id::text = e->>'id' AND k.project_id = v.project_id;

-- The agent's instructions and MCP servers live on its settings row (server_id NULL).
INSERT INTO operations_settings(project_id, agent)
SELECT project_id, agent_id FROM active_versions WHERE project_id IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO agent_configs(project_id, agent, instructions, inherit_skills)
SELECT project_id, agent_id, '', false FROM active_versions WHERE project_id IS NOT NULL ON CONFLICT DO NOTHING;
UPDATE agent_configs g SET instructions = coalesce(v.c->>'instructions', ''), revision = g.revision + 1
FROM active_versions v WHERE g.agent = v.agent_id AND g.project_id = v.project_id AND g.server_id IS NULL;
DELETE FROM agent_config_mcp_servers m USING agent_configs g, active_versions v
    WHERE m.config_id = g.id AND g.agent = v.agent_id AND g.server_id IS NULL;
INSERT INTO agent_config_mcp_servers(config_id, project_id, mcp_server_id)
SELECT DISTINCT g.id, g.project_id, m.id FROM active_versions v
JOIN agent_configs g ON g.agent = v.agent_id AND g.project_id = v.project_id AND g.server_id IS NULL
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'mcp'->'servers', '[]')) e
JOIN mcp_servers m ON m.id::text = e->>'id' AND m.project_id = g.project_id;

-- SSH: the selected servers, per-server overrides (an 'agent_version' text becomes a server override) and the
-- agent's own connections (credentials referenced as they were).
CREATE TEMPORARY TABLE active_servers ON COMMIT DROP AS
SELECT v.agent_id, v.project_id, s.id AS server_id, e AS server
FROM active_versions v
CROSS JOIN LATERAL jsonb_array_elements(coalesce(v.c->'ssh'->'servers', '[]')) e
JOIN servers s ON s.id::text = e->>'id' AND s.project_id = v.project_id;
DELETE FROM operations_servers o USING active_versions v WHERE o.agent = v.agent_id AND v.project_id IS NOT NULL;
INSERT INTO operations_servers(project_id, agent, server_id) SELECT project_id, agent_id, server_id FROM active_servers;
DELETE FROM agent_configs g USING active_versions v WHERE g.agent = v.agent_id AND g.server_id IS NOT NULL
    AND v.project_id IS NOT NULL;
INSERT INTO agent_configs(project_id, agent, server_id, instructions, inherit_skills, inherit_mcp_servers)
SELECT project_id, agent_id, server_id,
    CASE WHEN coalesce(server->>'instructions_source', 'agent') = 'agent' THEN NULL ELSE server->>'instructions' END,
    coalesce(server->>'skills_source', 'agent') = 'agent',
    coalesce(server->>'mcp_source', 'agent') = 'agent'
FROM active_servers
WHERE coalesce(server->>'instructions_source', 'agent') <> 'agent'
   OR coalesce(server->>'skills_source', 'agent') <> 'agent'
   OR coalesce(server->>'mcp_source', 'agent') <> 'agent';
INSERT INTO agent_config_skills(config_id, project_id, skill_id)
SELECT DISTINCT g.id, g.project_id, k.id FROM active_servers a
JOIN agent_configs g ON g.agent = a.agent_id AND g.server_id = a.server_id AND NOT g.inherit_skills
CROSS JOIN LATERAL jsonb_array_elements(coalesce(a.server->'skills', '[]')) e
JOIN agent_skills k ON k.id::text = e->>'id' AND k.project_id = g.project_id;
INSERT INTO agent_config_mcp_servers(config_id, project_id, mcp_server_id)
SELECT DISTINCT g.id, g.project_id, m.id FROM active_servers a
JOIN agent_configs g ON g.agent = a.agent_id AND g.server_id = a.server_id AND NOT g.inherit_mcp_servers
CROSS JOIN LATERAL jsonb_array_elements(coalesce(a.server->'mcp_servers', '[]')) e
JOIN mcp_servers m ON m.id::text = e->>'id' AND m.project_id = g.project_id;
DELETE FROM agent_server_connections c USING active_versions v
    WHERE c.agent_id = v.agent_id AND NOT EXISTS (SELECT 1 FROM active_servers a WHERE a.agent_id = c.agent_id
        AND a.server_id = c.server_id AND a.server->>'connection_source' = 'agent');
INSERT INTO agent_server_connections(agent_id, server_id, project_id, username, port, allow_sudo, known_hosts, credential_id)
SELECT a.agent_id, a.server_id, a.project_id, a.server->>'username', (a.server->>'port')::integer,
    (a.server->>'allow_sudo')::boolean, coalesce(a.server->>'known_hosts', ''),
    (SELECT k.id FROM credentials k WHERE k.id::text = a.server->'private_key_ref'->>'id'
        AND k.project_id = a.project_id AND k.kind = 'ssh_private_key')
FROM active_servers a WHERE a.server->>'connection_source' = 'agent'
ON CONFLICT (agent_id, server_id) DO UPDATE SET username = EXCLUDED.username, port = EXCLUDED.port,
    allow_sudo = EXCLUDED.allow_sudo, known_hosts = EXCLUDED.known_hosts, credential_id = EXCLUDED.credential_id,
    revision = agent_server_connections.revision + 1;

-- Tools: the version's rows; a version saved before 0078 gets 0078's rule from the (materialised) switches.
DELETE FROM agent_tools t USING active_versions v WHERE t.agent_id = v.agent_id;
INSERT INTO agent_tools(agent_id, tool, mode, usage, subagent_allowed)
SELECT v.agent_id, e->>'tool', e->>'mode', e->>'usage', coalesce((e->>'subagent_allowed')::boolean, true)
FROM active_versions v CROSS JOIN LATERAL jsonb_array_elements(v.c->'tools'->'items') e
WHERE v.runtime = 'openai' AND v.c ? 'tools';
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool FROM agents a JOIN active_versions v ON v.agent_id = a.id
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
WHERE v.runtime = 'openai' AND NOT v.c ? 'tools' AND t.offered;

-- Then the versions, the prompt history and every reference to them go.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE agents DROP CONSTRAINT agent_active_config_version;
ALTER TABLE agents DROP COLUMN active_config_version_id, DROP COLUMN next_config_version;
ALTER TABLE agent_sessions DROP CONSTRAINT session_agent_version;
ALTER TABLE agent_sessions DROP COLUMN agent_version_id;
DROP TABLE agent_versions;
DROP VIEW agent_prompt_version_details;
ALTER TABLE agent_prompts DROP CONSTRAINT active_prompt_version_fk;
DROP TABLE agent_prompt_versions;
DROP TABLE agent_prompts;
DROP FUNCTION guard_prompt_version();

-- Experiments are made on clones.
ALTER TABLE agents ADD COLUMN cloned_from text CONSTRAINT agent_cloned_from REFERENCES agents(id) ON DELETE SET NULL,
    ADD CONSTRAINT clone_of_another_agent CHECK (cloned_from <> id);
COMMENT ON COLUMN agents.cloned_from IS 'The agent this one was cloned from; Apply copies the clone''s configuration back.';

COMMIT;
