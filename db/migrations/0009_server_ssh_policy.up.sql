SET search_path = ka, public;

ALTER TABLE servers ADD COLUMN ssh_allowed boolean NOT NULL DEFAULT false;

CREATE OR REPLACE VIEW server_assets AS
SELECT id, project_id, name, hostname, description, tags, revision, created_at, updated_at,
    port AS ssh_port, username AS ssh_username, enabled AS access_enabled, allow_sudo,
    ssh_allowed
FROM servers;

COMMENT ON COLUMN servers.ssh_allowed IS
    'Operator permission for agent SSH access. Defaults off; does not configure credentials, enable a connection, or authorize sudo.';
