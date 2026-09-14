BEGIN;
SET search_path=ka,public;
ALTER TABLE service_deployments ADD COLUMN environment_id uuid;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_fk
    FOREIGN KEY (environment_id,project_id) REFERENCES environments(id,project_id)
    ON DELETE SET NULL (environment_id);
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_identity
    UNIQUE (service_id,server_id,environment_id,runtime_name) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX service_deployments_environment ON service_deployments(environment_id);
-- Resolve exact existing names only; retain unmatched labels without inventing environments or membership.
UPDATE service_deployments d SET environment_id=e.id FROM environments e
    WHERE d.project_id=e.project_id AND d.environment=e.name;
COMMENT ON COLUMN service_deployments.environment_id IS 'Selected environment; null only for legacy/unlinked deployment labels.';
COMMENT ON COLUMN service_deployments.environment IS 'Legacy label or name at selection time; linked deployments display the current environment name.';
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
        'id', d.id, 'server_id', d.server_id, 'environment_id', d.environment_id, 'environment', COALESCE(e.name,d.environment),
        'runtime', d.runtime, 'runtime_name', d.runtime_name, 'port', d.port,
        'log_location', d.log_location, 'deployed_version', d.deployed_version,
        'server', to_jsonb(h) - 'project_id')
        ORDER BY COALESCE(e.name,d.environment), h.name, d.runtime_name, d.id)
        FROM service_deployments d JOIN server_assets h ON h.id=d.server_id
        LEFT JOIN environments e ON e.id=d.environment_id
        WHERE d.service_id=s.id), '[]'::jsonb) AS deployments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',d.description)
        ORDER BY lower(other.name),other.id)
        FROM service_dependencies d JOIN services other ON other.id=d.depends_on_service_id
        WHERE d.service_id=s.id),'[]'::jsonb) AS dependencies,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',d.description)
        ORDER BY lower(other.name),other.id)
        FROM service_dependencies d JOIN services other ON other.id=d.service_id
        WHERE d.depends_on_service_id=s.id),'[]'::jsonb) AS dependents,
    s.service_type, s.service_role,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.id,'name',e.name) ORDER BY lower(e.name),e.id)
        FROM environment_services es JOIN environments e ON e.id=es.environment_id
        WHERE es.service_id=s.id),'[]'::jsonb) AS environments
FROM services s;
COMMIT;
