SET search_path = ka, public;

ALTER TABLE servers
    ADD COLUMN description text NOT NULL DEFAULT '',
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}',
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    ADD CONSTRAINT servers_tags_shape CHECK (
        cardinality(tags) <= 32 AND COALESCE(array_ndims(tags), 1) = 1
        AND array_position(tags, NULL) IS NULL
    );

-- Explicit public fields shared by server inventory and service context.
CREATE VIEW server_assets AS
SELECT id, project_id, name, hostname, description, tags, revision, created_at, updated_at,
    port AS ssh_port, username AS ssh_username, enabled AS access_enabled, allow_sudo
FROM servers;

CREATE OR REPLACE VIEW service_assets AS
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
        'server', to_jsonb(h) - 'project_id')
        ORDER BY d.environment, h.name, d.runtime_name, d.id)
        FROM service_deployments d JOIN server_assets h ON h.id=d.server_id
        WHERE d.service_id=s.id), '[]'::jsonb) AS deployments
FROM services s;

COMMENT ON VIEW server_assets IS 'Public server inventory fields; excludes credentials, host keys and connection errors.';
COMMENT ON COLUMN servers.tags IS 'Operator labels for searching inventory and describing hosts to agents.';
