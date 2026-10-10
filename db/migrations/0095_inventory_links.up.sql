BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;  -- settle pending graph link checks so the graph tables can be dropped

-- Connections between inventory entries: services, servers, environments and code projects (ka.repositories). Each
-- side has one nullable column per entry type and exactly one is set, so both ends are real foreign keys in the link's
-- project and deleting an entry deletes its links. from_kind/from_id and to_kind/to_id are derived for readers.
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

-- Documents attached to an inventory entry. The documents live in ka.documents; detaching never deletes them.
CREATE TABLE inventory_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    service_id uuid,
    server_id uuid,
    environment_id uuid,
    code_project_id uuid,
    document_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    entry_kind text GENERATED ALWAYS AS (CASE WHEN service_id IS NOT NULL THEN 'service'
        WHEN server_id IS NOT NULL THEN 'server' WHEN environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    entry_id uuid GENERATED ALWAYS AS (COALESCE(service_id, server_id, environment_id, code_project_id)) STORED,
    CONSTRAINT inventory_documents_one_entry CHECK (num_nonnulls(service_id, server_id, environment_id, code_project_id) = 1),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX inventory_documents_once ON inventory_documents (entry_id, document_id);
CREATE INDEX inventory_documents_document ON inventory_documents (document_id);
CREATE INDEX inventory_documents_project ON inventory_documents (project_id);

-- Every inventory entry with its kind and name, for naming link ends.
CREATE VIEW inventory_entries AS
SELECT 'service'::text AS kind, id, project_id, name::text AS name FROM services
UNION ALL SELECT 'server', id, project_id, name::text FROM servers
UNION ALL SELECT 'environment', id, project_id, name::text FROM environments
UNION ALL SELECT 'code_project', id, project_id, name::text FROM repositories;

INSERT INTO inventory_links (project_id, from_service_id, to_service_id, kind, description, created_at, updated_at)
SELECT project_id, service_id, depends_on_service_id, 'depends_on', description, created_at, updated_at
FROM service_dependencies;
INSERT INTO inventory_links (project_id, from_service_id, to_code_project_id, kind)
SELECT project_id, service_id, repository_id, 'built_from' FROM service_repositories;

-- Graph attachments move to the entry behind the node (a deployment's go to its service); hand-drawn nodes' go away.
INSERT INTO inventory_documents (project_id, service_id, server_id, environment_id, code_project_id, document_id, created_at)
SELECT l.project_id, COALESCE(n.service_id, d.service_id), n.server_id,
    CASE WHEN n.server_id IS NULL THEN n.environment_id END, n.repository_id, l.document_id, min(l.created_at)
FROM kg_node_documents l
JOIN kg_nodes n ON n.id = l.node_id
LEFT JOIN service_deployments d ON d.id = n.deployment_id
WHERE num_nonnulls(n.server_id, n.environment_id, n.service_id, n.repository_id, n.deployment_id) > 0
GROUP BY 1, 2, 3, 4, 5, 6;

CREATE OR REPLACE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner, s.operations_notes,
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

-- The stored system graph (0092-0094) and the two link tables it replaces.
DROP TRIGGER kg_graph_changed ON documents;
DROP TABLE kg_node_documents;
DROP TABLE kg_edges;
DROP TABLE kg_nodes;
DROP TABLE kg_graph_version;
DROP FUNCTION kg_document_changed();
DROP FUNCTION kg_graph_changed();
DROP FUNCTION kg_edges_check();
DROP FUNCTION kg_nodes_place();
DROP TABLE service_dependencies;
DROP TABLE service_repositories;

COMMIT;
