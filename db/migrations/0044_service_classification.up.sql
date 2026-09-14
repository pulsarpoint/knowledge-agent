BEGIN;
SET search_path=ka,public;

CREATE TYPE service_type AS ENUM ('unclassified','custom','builtin','external');
CREATE TYPE service_role AS ENUM ('unclassified','application','proxy','web_server','database','cache','message_broker','worker','other');
ALTER TABLE services
    ADD COLUMN service_type service_type NOT NULL DEFAULT 'unclassified',
    ADD COLUMN service_role service_role NOT NULL DEFAULT 'unclassified';
COMMENT ON COLUMN services.service_type IS 'Software origin: custom application, built-in third-party software, or external service. Existing inventory is unclassified until an operator selects a type.';
COMMENT ON COLUMN services.service_role IS 'Primary architectural role, used for graph labels and colors; independent of software origin.';

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
    s.service_type, s.service_role
FROM services s;
COMMIT;
