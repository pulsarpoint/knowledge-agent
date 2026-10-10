BEGIN;
SET search_path = ka, public;

-- The inventory (Servers, Services with their deployments, Environments, Code projects) is the source of truth; graph
-- nodes link to their rows and triggers keep the graph in step, in the writer's transaction.
ALTER TABLE kg_nodes
    ADD COLUMN server_id uuid,
    ADD COLUMN deployment_id uuid,
    ADD COLUMN environment_id uuid,
    ADD COLUMN service_id uuid,
    ADD COLUMN repository_id uuid,
    ADD CONSTRAINT kg_nodes_server FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE SET NULL (server_id),
    ADD CONSTRAINT kg_nodes_deployment FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE SET NULL (deployment_id),
    ADD CONSTRAINT kg_nodes_environment FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE SET NULL (environment_id),
    ADD CONSTRAINT kg_nodes_service FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE SET NULL (service_id),
    ADD CONSTRAINT kg_nodes_repository FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id) ON DELETE SET NULL (repository_id),
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1),
    ADD CONSTRAINT kg_nodes_source_layer CHECK (
        (server_id IS NULL AND deployment_id IS NULL AND environment_id IS NULL OR layer = 'infrastructure')
        AND (service_id IS NULL AND repository_id IS NULL OR layer = 'software'));
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_deployment ON kg_nodes (deployment_id) WHERE deployment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_service ON kg_nodes (service_id) WHERE service_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_repository ON kg_nodes (repository_id) WHERE repository_id IS NOT NULL;
COMMENT ON COLUMN kg_nodes.server_id IS 'Synced from Servers: name, kind, place and existence follow the row.';

ALTER TABLE kg_edges
    ADD COLUMN synced boolean NOT NULL DEFAULT false,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores', 'includes'));
COMMENT ON COLUMN kg_edges.synced IS 'Made by the inventory sync (dependencies, code projects, environment members, deployments); read-only in the graph.';

-- The node linked to an inventory row (link = one of the five column names).
CREATE FUNCTION kg_node_for(link text, row_id uuid) RETURNS uuid LANGUAGE plpgsql STABLE SET search_path = ka, pg_temp AS $$
DECLARE found uuid;
BEGIN
    IF link NOT IN ('server_id', 'deployment_id', 'environment_id', 'service_id', 'repository_id') THEN
        RAISE EXCEPTION 'unknown graph link %', link;
    END IF;
    EXECUTE format('SELECT id FROM kg_nodes WHERE %I = $1', link) INTO found USING row_id;
    RETURN found;
END $$;

-- The first free sibling name: name, name (2), name (3), … (case-insensitive, at most 160 characters).
CREATE FUNCTION kg_free_name(project uuid, the_layer text, parent uuid, wanted text, self uuid) RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = ka, pg_temp AS $$
DECLARE
    candidate text := left(btrim(wanted), 160);
    n integer := 1;
BEGIN
    WHILE EXISTS (SELECT 1 FROM kg_nodes WHERE project_id = project AND layer = the_layer
            AND parent_id IS NOT DISTINCT FROM parent AND lower(name) = lower(candidate) AND id IS DISTINCT FROM self) LOOP
        n := n + 1;
        candidate := left(btrim(wanted), 160 - length(' (' || n || ')')) || ' (' || n || ')';
    END LOOP;
    RETURN candidate;
END $$;

CREATE FUNCTION kg_sync_edge(project uuid, from_node uuid, to_node uuid, edge_type text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF from_node IS NULL OR to_node IS NULL OR from_node = to_node THEN
        RETURN;
    END IF;
    -- A hand-made edge with the same ends and type stays hand-made.
    INSERT INTO kg_edges(project_id, from_id, to_id, type, synced) VALUES (project, from_node, to_node, edge_type, true)
        ON CONFLICT ON CONSTRAINT kg_edges_once DO NOTHING;
END $$;

CREATE FUNCTION kg_unsync_edge(from_node uuid, to_node uuid, edge_type text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    DELETE FROM kg_edges WHERE synced AND from_id = from_node AND to_id = to_node AND type = edge_type;
END $$;

-- Creates or updates the node of one inventory row and returns its id (NULL when the row does not exist).
CREATE FUNCTION kg_sync_row(link text, row_id uuid) RETURNS uuid LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    project uuid;
    wanted text;
    the_layer text;
    the_kind text;
    parent uuid;
    server uuid;
    owner uuid;
    found uuid;
    current_name text;
    current_parent uuid;
    final_name text;
BEGIN
    CASE link
        WHEN 'server_id' THEN
            SELECT s.project_id, s.name INTO project, wanted FROM servers s WHERE s.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'server';
        WHEN 'environment_id' THEN
            SELECT e.project_id, e.name INTO project, wanted FROM environments e WHERE e.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'environment';
        WHEN 'service_id' THEN
            SELECT s.project_id, s.name INTO project, wanted FROM services s WHERE s.id = row_id;
            the_layer := 'software'; the_kind := 'service';
        WHEN 'repository_id' THEN
            SELECT r.project_id, r.name INTO project, wanted FROM repositories r WHERE r.id = row_id;
            the_layer := 'software'; the_kind := 'repository';
        WHEN 'deployment_id' THEN
            SELECT d.project_id, coalesce(nullif(btrim(d.runtime_name), ''), s.name) || ' · ' || d.environment,
                   d.server_id, d.service_id
                INTO project, wanted, server, owner
                FROM service_deployments d JOIN services s ON s.id = d.service_id WHERE d.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'instance';
        ELSE
            RAISE EXCEPTION 'unknown graph link %', link;
    END CASE;
    IF project IS NULL THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project::text));
    IF link = 'deployment_id' THEN
        parent := kg_sync_row('server_id', server);
    END IF;
    found := kg_node_for(link, row_id);
    IF found IS NULL THEN
        EXECUTE format('INSERT INTO kg_nodes(project_id, layer, kind, name, parent_id, %I) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id', link)
            INTO found USING project, the_layer, the_kind, kg_free_name(project, the_layer, parent, wanted, NULL), parent, row_id;
    ELSE
        SELECT n.name, n.parent_id INTO current_name, current_parent FROM kg_nodes n WHERE n.id = found;
        final_name := kg_free_name(project, the_layer, parent, wanted, found);
        IF current_name IS DISTINCT FROM final_name OR current_parent IS DISTINCT FROM parent THEN
            UPDATE kg_nodes SET name = final_name, parent_id = parent, revision = revision + 1 WHERE id = found;
        END IF;
    END IF;
    IF link = 'deployment_id' THEN
        DELETE FROM kg_edges WHERE synced AND from_id = found AND type = 'instance_of'
            AND to_id IS DISTINCT FROM kg_node_for('service_id', owner);
        PERFORM kg_sync_edge(project, found, kg_sync_row('service_id', owner), 'instance_of');
    END IF;
    RETURN found;
END $$;

-- A linked row is going away: its synced edges go; its node goes too unless something hand-made hangs off it, in which
-- case the node stays, unlinked, with a note.
CREATE FUNCTION kg_sync_release(link text, row_id uuid, label text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    found uuid := kg_node_for(link, row_id);
    project uuid;
BEGIN
    IF found IS NULL THEN
        RETURN;
    END IF;
    SELECT n.project_id INTO project FROM kg_nodes n WHERE n.id = found;
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project::text));
    DELETE FROM kg_edges WHERE synced AND (from_id = found OR to_id = found);
    IF EXISTS (SELECT 1 FROM kg_nodes WHERE parent_id = found) OR EXISTS (SELECT 1 FROM kg_node_documents WHERE node_id = found) THEN
        EXECUTE format($sql$UPDATE kg_nodes SET %I = NULL, revision = revision + 1,
            description = right(CASE WHEN description = '' THEN '' ELSE description || E'\n' END
                || 'Removed from ' || $2 || ' on ' || to_char(now(), 'YYYY-MM-DD') || '.', 4000)
            WHERE id = $1$sql$, link) USING found, label;
    ELSE
        DELETE FROM kg_nodes WHERE id = found;
    END IF;
END $$;

-- Row triggers on the five inventory tables. TG_ARGV: link column, delete label.
CREATE FUNCTION kg_sync_inventory() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    link text := TG_ARGV[0];
    deployment record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM kg_sync_release(link, OLD.id, TG_ARGV[1]);
        RETURN OLD;
    END IF;
    PERFORM kg_sync_row(link, NEW.id);
    IF link = 'service_id' AND TG_OP = 'UPDATE' THEN  -- deployment names use the service name
        FOR deployment IN SELECT id FROM service_deployments WHERE service_id = NEW.id LOOP
            PERFORM kg_sync_row('deployment_id', deployment.id);
        END LOOP;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON servers FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('server_id', 'Servers');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON servers FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('server_id', 'Servers');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON environments FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('environment_id', 'Environments');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON environments FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('environment_id', 'Environments');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON services FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('service_id', 'Services');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON services FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('service_id', 'Services');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON repositories FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('repository_id', 'Code projects');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON repositories FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('repository_id', 'Code projects');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF server_id, service_id, runtime_name, environment ON service_deployments
    FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('deployment_id', 'Services (deployment)');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON service_deployments
    FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('deployment_id', 'Services (deployment)');

-- Relation tables become synced edges. TG_ARGV: from link, from column, to link, to column, edge type.
CREATE FUNCTION kg_sync_relation() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    r jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        r := to_jsonb(OLD);
        PERFORM kg_unsync_edge(kg_node_for(TG_ARGV[0], (r ->> TG_ARGV[1])::uuid), kg_node_for(TG_ARGV[2], (r ->> TG_ARGV[3])::uuid), TG_ARGV[4]);
        RETURN OLD;
    END IF;
    r := to_jsonb(NEW);
    PERFORM kg_sync_edge((r ->> 'project_id')::uuid, kg_sync_row(TG_ARGV[0], (r ->> TG_ARGV[1])::uuid),
                         kg_sync_row(TG_ARGV[2], (r ->> TG_ARGV[3])::uuid), TG_ARGV[4]);
    RETURN NEW;
END $$;

CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON service_dependencies
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('service_id', 'service_id', 'service_id', 'depends_on_service_id', 'depends_on');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON service_repositories
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('service_id', 'service_id', 'repository_id', 'repository_id', 'uses');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON environment_servers
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('environment_id', 'environment_id', 'server_id', 'server_id', 'includes');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON environment_services
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('environment_id', 'environment_id', 'service_id', 'service_id', 'includes');

-- Backfill what the inventory already holds.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT id FROM servers ORDER BY created_at, id LOOP PERFORM kg_sync_row('server_id', r.id); END LOOP;
    FOR r IN SELECT id FROM environments ORDER BY created_at, id LOOP PERFORM kg_sync_row('environment_id', r.id); END LOOP;
    FOR r IN SELECT id FROM services ORDER BY created_at, id LOOP PERFORM kg_sync_row('service_id', r.id); END LOOP;
    FOR r IN SELECT id FROM repositories ORDER BY created_at, id LOOP PERFORM kg_sync_row('repository_id', r.id); END LOOP;
    FOR r IN SELECT id FROM service_deployments ORDER BY id LOOP PERFORM kg_sync_row('deployment_id', r.id); END LOOP;
    FOR r IN SELECT * FROM service_dependencies LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('service_id', r.service_id), kg_node_for('service_id', r.depends_on_service_id), 'depends_on');
    END LOOP;
    FOR r IN SELECT * FROM service_repositories LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('service_id', r.service_id), kg_node_for('repository_id', r.repository_id), 'uses');
    END LOOP;
    FOR r IN SELECT * FROM environment_servers LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('environment_id', r.environment_id), kg_node_for('server_id', r.server_id), 'includes');
    END LOOP;
    FOR r IN SELECT * FROM environment_services LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('environment_id', r.environment_id), kg_node_for('service_id', r.service_id), 'includes');
    END LOOP;
END $$;

COMMIT;
