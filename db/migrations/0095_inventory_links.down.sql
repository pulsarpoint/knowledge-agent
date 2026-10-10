BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inventory_links WHERE kind = 'connects_to' OR attributes <> '{}'::jsonb
            OR (kind = 'built_from' AND description <> ''))
        OR EXISTS (SELECT 1 FROM inventory_documents) THEN
        RAISE EXCEPTION 'Cannot roll back 0095: remove connects_to links, link attributes, built_from descriptions and attached documents first.';
    END IF;
END $$;

-- service_repositories as 0003 made it, service_dependencies as 0042 made it.
CREATE TABLE service_repositories (
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    repository_id uuid NOT NULL,
    PRIMARY KEY (service_id, repository_id),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id)
);
CREATE INDEX service_repositories_repository ON service_repositories(repository_id);
CREATE TABLE service_dependencies (
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    depends_on_service_id uuid NOT NULL,
    description text NOT NULL DEFAULT '' CHECK (length(description)<=4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service_id,depends_on_service_id),
    CHECK (service_id<>depends_on_service_id),
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX service_dependencies_depends_on ON service_dependencies(depends_on_service_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON service_dependencies
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE service_dependencies IS 'Directed inventory relationships: service_id depends on depends_on_service_id. Mutual dependencies are allowed.';
COMMENT ON COLUMN service_dependencies.description IS 'Why the source service uses the target service, for example stores orders or publishes events. Operator context, not executable instructions.';

INSERT INTO service_repositories (project_id, service_id, repository_id)
SELECT project_id, from_service_id, to_code_project_id FROM inventory_links WHERE kind = 'built_from';
INSERT INTO service_dependencies (project_id, service_id, depends_on_service_id, description, created_at, updated_at)
SELECT project_id, from_service_id, to_service_id, description, created_at, updated_at FROM inventory_links WHERE kind = 'depends_on';

DROP VIEW service_assets;
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

DROP VIEW inventory_entries;
DROP TABLE inventory_documents;
DROP TABLE inventory_links;

-- The stored system graph (0092, 0093, 0094), empty. After a rollback the old code's start-up sync refills it.
-- The system graph: four layers (viewpoints), each a tree of nodes — graph in graph, every node is a graph of its
-- children. A node has one parent, in the same project and layer (NULL = top level; the project is the implicit root).
-- Edges join any two nodes of the project, in one layer or across layers, and are stored at the deepest level anyone
-- knows them; the API computes higher levels and never stores them.
CREATE TABLE kg_nodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    layer text NOT NULL CONSTRAINT kg_nodes_layer CHECK (layer IN ('business', 'software', 'data', 'infrastructure')),
    kind nonblank NOT NULL CONSTRAINT kg_nodes_kind CHECK (length(kind) <= 60),
    name nonblank NOT NULL CONSTRAINT kg_nodes_name CHECK (length(name) <= 160),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_nodes_description CHECK (length(description) <= 4000),
    parent_id uuid,
    properties jsonb NOT NULL DEFAULT '{}' CONSTRAINT kg_nodes_properties
        CHECK (jsonb_typeof(properties) = 'object' AND length(properties::text) <= 16384),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    UNIQUE (id, project_id, layer),
    CONSTRAINT kg_nodes_parent_layer FOREIGN KEY (parent_id, project_id, layer)
        REFERENCES kg_nodes(id, project_id, layer) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX kg_nodes_sibling_name ON kg_nodes (project_id, layer, parent_id, lower(name)) NULLS NOT DISTINCT;
CREATE INDEX kg_nodes_children ON kg_nodes (parent_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_nodes FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN kg_nodes.kind IS 'Free text such as service, module, server, table, capability; the UI suggests kinds per layer.';
COMMENT ON COLUMN kg_nodes.properties IS 'Extra facts about the node (port, language, …). Operator context, not instructions.';

-- No node inside itself, and no branch deeper than 12 levels (top level = 1). Placing nodes is serialised per project
-- so two concurrent moves cannot build a cycle together. Errors carry no constraint name; the API shows the sentence.
CREATE FUNCTION kg_nodes_place() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    above integer := 0;
    inside boolean := false;
    height integer := 1;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(NEW.project_id::text));
    IF NEW.parent_id IS NOT NULL THEN
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.parent_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT count(*), coalesce(bool_or(id = NEW.id), false) INTO above, inside FROM up;
        IF inside OR NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'A node cannot be moved inside itself.' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        WITH RECURSIVE down(id, level) AS (
            SELECT NEW.id, 1
            UNION ALL
            SELECT n.id, down.level + 1 FROM kg_nodes n JOIN down ON n.parent_id = down.id)
        SELECT max(level) INTO height FROM down;
    END IF;
    IF above + height > 12 THEN
        RAISE EXCEPTION 'Too deep: a layer has at most 12 levels.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_nodes_place BEFORE INSERT OR UPDATE OF parent_id ON kg_nodes
    FOR EACH ROW EXECUTE FUNCTION kg_nodes_place();

CREATE TABLE kg_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_id uuid NOT NULL,
    to_id uuid NOT NULL,
    type text NOT NULL CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes',
        'publishes', 'subscribes', 'instance_of', 'runs_on', 'implements', 'stores')),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_edges_description CHECK (length(description) <= 4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kg_edges_two_nodes CHECK (from_id <> to_id),
    CONSTRAINT kg_edges_once UNIQUE (from_id, to_id, type),
    FOREIGN KEY (from_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_edges_to ON kg_edges (to_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_edges FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE kg_edges IS 'Directed, typed edges between graph nodes. Adding a type is a migration (each type has a diagram style).';

-- Containment already says "inside": an edge between a node and its ancestor or descendant is refused when it is made.
-- A later move may still produce one; views count it as internal. A self-edge is left to kg_edges_two_nodes.
CREATE FUNCTION kg_edges_check() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.from_id = NEW.to_id THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.from_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.to_id)
    OR EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.to_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.from_id) THEN
        RAISE EXCEPTION 'An edge cannot join a node and a node that contains it.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_edges_check BEFORE INSERT ON kg_edges FOR EACH ROW EXECUTE FUNCTION kg_edges_check();

-- Documents attached to a node (the documents themselves live in ka.documents; detaching never deletes them).
CREATE TABLE kg_node_documents (
    node_id uuid NOT NULL,
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, document_id),
    FOREIGN KEY (node_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_node_documents_document ON kg_node_documents (document_id);

-- Graph nodes can link to inventory rows (Servers, Services and their deployments, Environments, Code projects). The
-- admin API's sync (graph_sync.py) keeps them in step in the writer's transaction. The links are checked at commit, so
-- the sync can still see which node lost its row after a delete — and a delete path that forgot the sync fails loudly.
ALTER TABLE kg_nodes
    ADD COLUMN server_id uuid,
    ADD COLUMN deployment_id uuid,
    ADD COLUMN environment_id uuid,
    ADD COLUMN service_id uuid,
    ADD COLUMN repository_id uuid,
    ADD CONSTRAINT kg_nodes_server FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_deployment FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_environment FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_service FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_repository FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1),
    ADD CONSTRAINT kg_nodes_source_layer CHECK (
        (server_id IS NULL AND deployment_id IS NULL AND environment_id IS NULL OR layer = 'infrastructure')
        AND (service_id IS NULL AND repository_id IS NULL OR layer = 'software'));
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_deployment ON kg_nodes (deployment_id) WHERE deployment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_service ON kg_nodes (service_id) WHERE service_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_repository ON kg_nodes (repository_id) WHERE repository_id IS NOT NULL;

ALTER TABLE kg_edges
    ADD COLUMN synced boolean NOT NULL DEFAULT false,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores', 'includes'));
COMMENT ON COLUMN kg_edges.synced IS 'Made by the inventory sync (dependencies, code projects, environment members, deployments); read-only in the graph.';

-- One number that changes whenever the graph does: processes keep the graph in memory and rebuild it when it moves.
CREATE TABLE kg_graph_version (
    "only" boolean PRIMARY KEY DEFAULT true CHECK ("only"),
    version bigint NOT NULL DEFAULT 0
);
INSERT INTO kg_graph_version DEFAULT VALUES;

CREATE FUNCTION kg_graph_changed() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE next_version bigint;
BEGIN
    UPDATE kg_graph_version SET version = version + 1 RETURNING version INTO next_version;
    PERFORM pg_notify('ka_graph', next_version::text);
    RETURN NULL;
END $$;
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_nodes FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_edges FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_node_documents FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();
-- Editing an attached document (new version, slug, folder) changes the titles and slugs the graph snapshot holds;
-- editing a document no node uses leaves the graph alone.
CREATE FUNCTION kg_document_changed() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE next_version bigint;
BEGIN
    IF EXISTS (SELECT 1 FROM kg_node_documents WHERE document_id = NEW.id) THEN
        UPDATE kg_graph_version SET version = version + 1 RETURNING version INTO next_version;
        PERFORM pg_notify('ka_graph', next_version::text);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER kg_graph_changed AFTER UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION kg_document_changed();

-- Environments contain their servers in the infrastructure graph. A server gets one node per environment it is in
-- (server_id + environment_id), and a top-level node (environment_id NULL) when it is in none.
ALTER TABLE kg_nodes
    DROP CONSTRAINT kg_nodes_one_source,
    ADD CONSTRAINT kg_nodes_one_source CHECK (
        num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1
        OR (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) = 2
            AND server_id IS NOT NULL AND environment_id IS NOT NULL));
DROP INDEX kg_nodes_by_server;
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id, environment_id) NULLS NOT DISTINCT WHERE server_id IS NOT NULL;
DROP INDEX kg_nodes_by_environment;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL AND server_id IS NULL;


COMMIT;
