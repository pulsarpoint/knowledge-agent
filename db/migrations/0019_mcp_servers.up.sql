SET search_path = ka, public;

CREATE TABLE mcp_servers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 4000),
    url text NOT NULL CHECK (char_length(url) <= 2000),
    token_ciphertext bytea,
    enabled boolean NOT NULL DEFAULT true,
    tools jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(tools) = 'array'),
    last_tested_at timestamptz,
    last_test_error text,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(project_id, name),
    UNIQUE(id, project_id)
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON mcp_servers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

ALTER TABLE agent_configs ADD COLUMN inherit_mcp_servers boolean NOT NULL DEFAULT false;
UPDATE agent_configs SET inherit_mcp_servers=true WHERE server_id IS NOT NULL;
ALTER TABLE agent_configs ADD CHECK (server_id IS NOT NULL OR NOT inherit_mcp_servers);
CREATE TABLE agent_config_mcp_servers (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    mcp_server_id uuid NOT NULL,
    PRIMARY KEY(config_id, mcp_server_id),
    FOREIGN KEY(config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY(mcp_server_id, project_id) REFERENCES mcp_servers(id, project_id) ON DELETE CASCADE
);
