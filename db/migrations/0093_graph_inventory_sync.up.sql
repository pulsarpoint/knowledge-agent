BEGIN;
SET search_path = ka, public;

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

COMMIT;
