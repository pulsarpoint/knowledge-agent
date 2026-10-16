BEGIN;
SET search_path = ka, public;

-- The old storage comes back (schema as before 0114) holding each agent's references: a kind is switched on when the
-- agent references it, references to rows that no longer exist and kinds without a table (environments, documents)
-- are left out, the agent's instructions and MCP servers go to its agent-level configuration. Per-server overrides
-- and per-agent SSH connections are not restored.
ALTER TABLE agents
    ADD COLUMN all_collections boolean NOT NULL DEFAULT false,
    ADD COLUMN all_repositories boolean NOT NULL DEFAULT false,
    ADD COLUMN documents_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN repositories_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN ssh_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN skills_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN mcp_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN services_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN resources text NOT NULL DEFAULT 'fixed' CONSTRAINT agent_resources_known CHECK (resources IN ('fixed', 'per_run'));
COMMENT ON COLUMN agents.resources IS
    'fixed: the agent always uses its selections. per_run: its selections are what a run may be given; each run passes its own.';

CREATE TABLE agent_repositories (
    agent_id text NOT NULL REFERENCES agents(id),
    repository_id uuid NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, repository_id)
);

CREATE TABLE agent_collections (
    agent_id text NOT NULL REFERENCES agents(id),
    collection_id uuid NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, collection_id)
);

CREATE TABLE agent_services (
    agent_id text NOT NULL REFERENCES agents(id),
    service_id uuid NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, service_id)
);

CREATE TABLE skills_agents (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_id uuid NOT NULL,
    project_id uuid NOT NULL,
    PRIMARY KEY (agent_id, skill_id),
    FOREIGN KEY (skill_id, project_id) REFERENCES agent_skills(id, project_id) ON DELETE CASCADE
);
CREATE INDEX skills_agents_skill_id ON skills_agents(skill_id);
COMMENT ON TABLE skills_agents IS 'Skills selected for each agent. Disabling skills preserves these assignments.';

CREATE TABLE operations_settings (
    project_id uuid NOT NULL REFERENCES projects(id),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    agent text NOT NULL DEFAULT 'operations' REFERENCES agents(id),
    PRIMARY KEY (project_id, agent)
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON operations_settings FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE operations_servers (
    project_id uuid NOT NULL,
    server_id uuid NOT NULL,
    agent text NOT NULL DEFAULT 'operations' REFERENCES agents(id),
    PRIMARY KEY (project_id, agent, server_id),
    FOREIGN KEY (project_id, agent) REFERENCES operations_settings(project_id, agent),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE
);

CREATE TABLE agent_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    agent text NOT NULL REFERENCES agents(id),
    server_id uuid,
    instructions text CHECK (char_length(instructions) <= 40000),
    inherit_skills boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    inherit_mcp_servers boolean NOT NULL DEFAULT false,
    CONSTRAINT agent_configs_check CHECK (server_id IS NOT NULL OR (instructions IS NOT NULL AND NOT inherit_skills)),
    CONSTRAINT agent_configs_check2 CHECK (server_id IS NOT NULL OR NOT inherit_mcp_servers),
    UNIQUE (id, project_id),
    UNIQUE NULLS NOT DISTINCT (project_id, agent, server_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_configs FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE agent_config_skills (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    PRIMARY KEY (config_id, skill_id),
    FOREIGN KEY (config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id, project_id) REFERENCES agent_skills(id, project_id) ON DELETE CASCADE
);
COMMENT ON TABLE agent_config_skills IS 'Explicit skill selections for server-specific agent configurations.';

CREATE TABLE agent_config_mcp_servers (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    mcp_server_id uuid NOT NULL,
    PRIMARY KEY (config_id, mcp_server_id),
    FOREIGN KEY (config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (mcp_server_id, project_id) REFERENCES mcp_servers(id, project_id) ON DELETE CASCADE
);

CREATE TABLE agent_server_connections (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    server_id uuid NOT NULL,
    project_id uuid NOT NULL,
    username text CHECK (username ~ '^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}[$]?$'),
    port integer NOT NULL DEFAULT 22 CHECK (port BETWEEN 1 AND 65535),
    allow_sudo boolean NOT NULL DEFAULT false,
    known_hosts text NOT NULL DEFAULT '' CHECK (char_length(known_hosts) <= 40000),
    credential_id uuid,
    credential_kind text NOT NULL DEFAULT 'ssh_private_key' CHECK (credential_kind = 'ssh_private_key'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_tested_at timestamptz,
    last_test_error text,
    PRIMARY KEY (agent_id, server_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (credential_id, project_id, credential_kind) REFERENCES credentials(id, project_id, kind)
);
CREATE INDEX agent_server_connections_credential ON agent_server_connections(credential_id);
CREATE INDEX agent_server_connections_server ON agent_server_connections(server_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_server_connections FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE agent_server_connections IS
    'Optional per-agent SSH overrides. A missing row uses the server configuration; a present row is a complete override.';

CREATE TEMP TABLE agent_refs ON COMMIT DROP AS
SELECT a.id AS agent_id, r->>'type' AS type, r->>'id' AS id
FROM agents a, jsonb_array_elements(a.access->'resources') r;

UPDATE agents a SET
    ssh_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'server'),
    services_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'service'),
    repositories_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'code_project'),
    documents_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'collection'),
    skills_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'skill'),
    mcp_enabled = EXISTS (SELECT 1 FROM agent_refs r WHERE r.agent_id = a.id AND r.type = 'mcp_server');

INSERT INTO operations_settings(project_id, agent)
SELECT DISTINCT s.project_id, r.agent_id FROM agent_refs r JOIN servers s ON s.id::text = r.id WHERE r.type = 'server';
INSERT INTO operations_servers(project_id, agent, server_id)
SELECT DISTINCT s.project_id, r.agent_id, s.id FROM agent_refs r JOIN servers s ON s.id::text = r.id WHERE r.type = 'server';
INSERT INTO agent_services(agent_id, service_id)
SELECT DISTINCT r.agent_id, s.id FROM agent_refs r JOIN services s ON s.id::text = r.id WHERE r.type = 'service';
INSERT INTO agent_repositories(agent_id, repository_id)
SELECT DISTINCT r.agent_id, p.id FROM agent_refs r JOIN repositories p ON p.id::text = r.id WHERE r.type = 'code_project';
INSERT INTO agent_collections(agent_id, collection_id)
SELECT DISTINCT r.agent_id, c.id FROM agent_refs r JOIN rag_collections c ON c.id::text = r.id WHERE r.type = 'collection';
INSERT INTO skills_agents(agent_id, skill_id, project_id)
SELECT DISTINCT r.agent_id, s.id, s.project_id FROM agent_refs r JOIN agent_skills s ON s.id::text = r.id
WHERE r.type = 'skill';

INSERT INTO agent_configs(project_id, agent, instructions, inherit_skills)
SELECT p.id, a.id, a.instructions, false
FROM agents a CROSS JOIN (SELECT id FROM projects ORDER BY created_at LIMIT 1) p;
INSERT INTO agent_config_mcp_servers(config_id, project_id, mcp_server_id)
SELECT DISTINCT g.id, g.project_id, m.id FROM agent_refs r
JOIN agent_configs g ON g.agent = r.agent_id AND g.server_id IS NULL
JOIN mcp_servers m ON m.id::text = r.id AND m.project_id = g.project_id
WHERE r.type = 'mcp_server';

COMMIT;
