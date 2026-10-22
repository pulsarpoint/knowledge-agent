BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;
-- Recreates the old structures as they were before 0125, backfilled from the new model: the free-text environment
-- label from the environment's name, membership from deployments, inventory links from relations (depends_on,
-- built_from) and from connections between deployments (one connects_to per pair of services, with the port and
-- protocol of the oldest such connection as attributes). Lost: part_of relations and connections to external hosts
-- (0125 down drops them with their tables).

ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_environment_identity;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_identity
    UNIQUE (service_id, server_id, environment_id, runtime_name) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE service_deployments ADD COLUMN environment nonblank;
UPDATE service_deployments d SET environment = e.name FROM environments e WHERE e.id = d.environment_id;
ALTER TABLE service_deployments ALTER COLUMN environment SET NOT NULL;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key
    UNIQUE (service_id, server_id, environment, runtime_name) DEFERRABLE INITIALLY DEFERRED;
COMMENT ON COLUMN service_deployments.environment IS 'Legacy label or name at selection time; linked deployments display the current environment name.';

-- Membership as 0045 made it.
CREATE TABLE environment_services (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    service_id uuid NOT NULL,
    PRIMARY KEY (environment_id,service_id),
    FOREIGN KEY (environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX environment_services_service ON environment_services(service_id);
CREATE TABLE environment_servers (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    server_id uuid NOT NULL,
    PRIMARY KEY (environment_id,server_id),
    FOREIGN KEY (environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id,project_id) REFERENCES servers(id,project_id) ON DELETE CASCADE
);
CREATE INDEX environment_servers_server ON environment_servers(server_id);
COMMENT ON TABLE environments IS 'Named architecture environments. Membership is maintained explicitly, not inferred from deployment labels.';
COMMENT ON TABLE environment_services IS 'Services belonging to an environment; membership alone does not describe dependencies or deployments.';
COMMENT ON TABLE environment_servers IS 'Servers belonging to an environment; membership grants no SSH access.';
INSERT INTO environment_services (project_id, environment_id, service_id)
SELECT DISTINCT project_id, environment_id, service_id FROM service_deployments;
INSERT INTO environment_servers (project_id, environment_id, server_id)
SELECT DISTINCT project_id, environment_id, server_id FROM service_deployments;

-- Inventory links as 0095 made them.
CREATE TABLE inventory_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_service_id uuid,
    from_server_id uuid,
    from_environment_id uuid,
    from_code_project_id uuid,
    to_service_id uuid,
    to_server_id uuid,
    to_environment_id uuid,
    to_code_project_id uuid,
    kind text NOT NULL CONSTRAINT inventory_links_kind CHECK (kind IN ('depends_on', 'built_from', 'connects_to')),
    description text NOT NULL DEFAULT '' CONSTRAINT inventory_links_description CHECK (length(description) <= 4000),
    attributes jsonb NOT NULL DEFAULT '{}' CONSTRAINT inventory_links_attributes CHECK (jsonb_typeof(attributes) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    from_kind text GENERATED ALWAYS AS (CASE WHEN from_service_id IS NOT NULL THEN 'service'
        WHEN from_server_id IS NOT NULL THEN 'server' WHEN from_environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    from_id uuid GENERATED ALWAYS AS (COALESCE(from_service_id, from_server_id, from_environment_id, from_code_project_id)) STORED,
    to_kind text GENERATED ALWAYS AS (CASE WHEN to_service_id IS NOT NULL THEN 'service'
        WHEN to_server_id IS NOT NULL THEN 'server' WHEN to_environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    to_id uuid GENERATED ALWAYS AS (COALESCE(to_service_id, to_server_id, to_environment_id, to_code_project_id)) STORED,
    UNIQUE (id, project_id),
    CONSTRAINT inventory_links_one_from CHECK (num_nonnulls(from_service_id, from_server_id, from_environment_id, from_code_project_id) = 1),
    CONSTRAINT inventory_links_one_to CHECK (num_nonnulls(to_service_id, to_server_id, to_environment_id, to_code_project_id) = 1),
    CONSTRAINT inventory_links_not_self CHECK (COALESCE(from_service_id, from_server_id, from_environment_id, from_code_project_id)
        <> COALESCE(to_service_id, to_server_id, to_environment_id, to_code_project_id)),
    CONSTRAINT inventory_links_kind_ends CHECK (kind = 'connects_to'
        OR (kind = 'depends_on' AND from_service_id IS NOT NULL AND to_service_id IS NOT NULL)
        OR (kind = 'built_from' AND from_service_id IS NOT NULL AND to_code_project_id IS NOT NULL)),
    FOREIGN KEY (from_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX inventory_links_once ON inventory_links (from_id, to_id, kind);
CREATE INDEX inventory_links_to ON inventory_links (to_id);
CREATE INDEX inventory_links_project ON inventory_links (project_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON inventory_links FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE inventory_links IS 'Directed connections between inventory entries: depends_on (service → service), built_from (service → code project), connects_to (any → any).';
COMMENT ON COLUMN inventory_links.description IS 'Why the connection exists. Operator context, not executable instructions.';
COMMENT ON COLUMN inventory_links.attributes IS 'String key/value facts such as protocol or port. Operator context, not instructions.';
INSERT INTO inventory_links (project_id, from_service_id, to_service_id, to_code_project_id, kind, description,
    created_by, created_at, updated_at)
SELECT project_id, from_service_id, to_service_id, to_code_project_id, kind, description, created_by, created_at, updated_at
FROM service_relations WHERE kind IN ('depends_on', 'built_from');
INSERT INTO inventory_links (project_id, from_service_id, to_service_id, kind, description, attributes, created_by,
    created_at, updated_at)
SELECT DISTINCT ON (a.service_id, b.service_id) c.project_id, a.service_id, b.service_id, 'connects_to', c.description,
    jsonb_strip_nulls(jsonb_build_object('port', c.port::text, 'protocol', c.protocol)), c.created_by, c.created_at,
    c.updated_at
FROM connections c
JOIN service_deployments a ON a.id = c.from_deployment_id
JOIN service_deployments b ON b.id = c.to_deployment_id
WHERE a.service_id <> b.service_id
ORDER BY a.service_id, b.service_id, c.created_at, c.id;

CREATE VIEW inventory_entries AS
SELECT 'service'::text AS kind, id, project_id, name::text AS name FROM services
UNION ALL SELECT 'server', id, project_id, name::text FROM servers
UNION ALL SELECT 'environment', id, project_id, name::text FROM environments
UNION ALL SELECT 'code_project', id, project_id, name::text FROM repositories;

-- The views as 0009, 0045 and 0109 left them.
CREATE VIEW server_assets AS
SELECT id, project_id, name, hostname, description, tags, revision, created_at, updated_at,
    port AS ssh_port, username AS ssh_username, enabled AS access_enabled, allow_sudo,
    ssh_allowed
FROM servers;
COMMENT ON VIEW server_assets IS 'Public server inventory fields; excludes credentials, host keys and connection errors.';

CREATE VIEW environment_assets AS
SELECT e.id,e.project_id,e.name,e.description,e.revision,e.created_at,e.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'name',s.name)
        ORDER BY lower(s.name),s.id)
        FROM environment_services es JOIN services s ON s.id=es.service_id
        WHERE es.environment_id=e.id),'[]'::jsonb) AS services,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'name',s.name,'hostname',s.hostname)
        ORDER BY lower(s.name),s.id)
        FROM environment_servers es JOIN servers s ON s.id=es.server_id
        WHERE es.environment_id=e.id),'[]'::jsonb) AS servers
FROM environments e;
COMMENT ON VIEW environment_assets IS 'Environment inventory and member summaries without credentials.';

CREATE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner,
    s.revision, s.created_at, s.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', r.id, 'name', r.name, 'remote_url', r.remote_url,
        'requested_ref', r.requested_ref, 'current_commit', r.current_commit,
        'checkout_path', r.checkout_path, 'synced_at', r.synced_at,
        'archived', r.archived_at IS NOT NULL) ORDER BY lower(r.name), r.id)
        FROM inventory_links l JOIN repositories r ON r.id = l.to_code_project_id
        WHERE l.kind = 'built_from' AND l.from_service_id = s.id), '[]'::jsonb) AS repositories,
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
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.to_service_id
        WHERE l.kind='depends_on' AND l.from_service_id=s.id),'[]'::jsonb) AS dependencies,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.from_service_id
        WHERE l.kind='depends_on' AND l.to_service_id=s.id),'[]'::jsonb) AS dependents,
    s.service_type, s.service_role,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.id,'name',e.name) ORDER BY lower(e.name),e.id)
        FROM environment_services es JOIN environments e ON e.id=es.environment_id
        WHERE es.service_id=s.id),'[]'::jsonb) AS environments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', l.id, 'direction', CASE WHEN l.from_id = s.id THEN 'outgoing' ELSE 'incoming' END,
        'kind', l.kind, 'entry_kind', o.kind, 'entry_id', o.id, 'name', o.name,
        'description', l.description, 'attributes', l.attributes)
        ORDER BY lower(o.name), o.id, l.id)
        FROM inventory_links l
        JOIN inventory_entries o ON o.id = CASE WHEN l.from_id = s.id THEN l.to_id ELSE l.from_id END
        WHERE l.kind = 'connects_to' AND (l.from_id = s.id OR l.to_id = s.id)), '[]'::jsonb) AS links
FROM services s;
COMMENT ON VIEW service_assets IS 'Scoped service context with repository snapshots and server inventory; excludes authentication secrets.';
COMMIT;
