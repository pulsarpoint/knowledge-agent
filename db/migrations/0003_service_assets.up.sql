SET search_path = ka, public;

-- A server can be inventoried before SSH access is configured.
ALTER TABLE servers ALTER COLUMN username DROP NOT NULL;
ALTER TABLE servers ADD CONSTRAINT servers_enabled_requires_username
    CHECK (NOT enabled OR username IS NOT NULL);

CREATE TABLE services (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    description text NOT NULL DEFAULT '',
    owner text NOT NULL DEFAULT '',
    operations_notes text NOT NULL DEFAULT '',
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id)
);
CREATE UNIQUE INDEX services_project_name ON services(project_id, lower(name));
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON services
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE service_repositories (
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    repository_id uuid NOT NULL,
    PRIMARY KEY (service_id, repository_id),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id)
);
CREATE INDEX service_repositories_repository ON service_repositories(repository_id);

CREATE TABLE service_deployments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    server_id uuid NOT NULL,
    environment nonblank NOT NULL,
    runtime text NOT NULL DEFAULT '',
    runtime_name text NOT NULL DEFAULT '',
    port integer CHECK (port BETWEEN 1 AND 65535),
    log_location text NOT NULL DEFAULT '',
    deployed_version text NOT NULL DEFAULT '',
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id),
    UNIQUE (service_id, server_id, environment, runtime_name)
);
CREATE INDEX service_deployments_server ON service_deployments(server_id);

-- One public projection for the admin UI and agent context. Never join credentials.
CREATE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner, s.operations_notes,
    s.revision, s.created_at, s.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', r.id, 'name', r.name, 'remote_url', r.remote_url,
        'requested_ref', r.requested_ref, 'current_commit', r.current_commit,
        'checkout_path', r.checkout_path, 'synced_at', r.synced_at,
        'archived', r.archived_at IS NOT NULL) ORDER BY lower(r.name), r.id)
        FROM service_repositories sr JOIN repositories r ON r.id=sr.repository_id
        WHERE sr.service_id=s.id), '[]'::jsonb) AS repositories,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', d.id, 'server_id', d.server_id, 'environment', d.environment,
        'runtime', d.runtime, 'runtime_name', d.runtime_name, 'port', d.port,
        'log_location', d.log_location, 'deployed_version', d.deployed_version,
        'server', jsonb_build_object('id', h.id, 'name', h.name, 'hostname', h.hostname,
            'ssh_port', h.port, 'ssh_username', h.username,
            'access_enabled', h.enabled, 'allow_sudo', h.allow_sudo))
        ORDER BY d.environment, h.name, d.runtime_name, d.id)
        FROM service_deployments d JOIN servers h ON h.id=d.server_id
        WHERE d.service_id=s.id), '[]'::jsonb) AS deployments
FROM services s;

COMMENT ON TABLE services IS 'Operator-maintained application inventory for agent investigations; not observed live health.';
COMMENT ON COLUMN services.operations_notes IS 'Untrusted operator context, never executable instructions or stored credentials.';
COMMENT ON COLUMN service_deployments.deployed_version IS 'Operator-reported running version; not inferred from a repository checkout.';
COMMENT ON VIEW service_assets IS 'Scoped service context with repository snapshots and server inventory; excludes authentication secrets.';
