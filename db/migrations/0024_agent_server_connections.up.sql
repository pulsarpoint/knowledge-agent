SET search_path = ka, public;

CREATE TABLE agent_server_connections (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    server_id uuid NOT NULL,
    project_id uuid NOT NULL,
    username text CHECK (username ~ '^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}[$]?$'),
    port integer NOT NULL DEFAULT 22 CHECK (port BETWEEN 1 AND 65535),
    allow_sudo boolean NOT NULL DEFAULT false,
    known_hosts text NOT NULL DEFAULT '' CHECK (char_length(known_hosts) <= 40000),
    credential_id uuid,
    credential_kind text NOT NULL DEFAULT 'ssh_private_key' CHECK (credential_kind='ssh_private_key'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_tested_at timestamptz,
    last_test_error text,
    PRIMARY KEY (agent_id, server_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (credential_id, project_id, credential_kind) REFERENCES credentials(id, project_id, kind)
);
CREATE INDEX agent_server_connections_server ON agent_server_connections(server_id);
CREATE INDEX agent_server_connections_credential ON agent_server_connections(credential_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_server_connections
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE agent_server_connections IS 'Optional per-agent SSH overrides. A missing row uses the server configuration; a present row is a complete override.';
