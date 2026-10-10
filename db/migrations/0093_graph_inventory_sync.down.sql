BEGIN;
SET search_path = ka, public;

DROP TRIGGER kg_sync ON servers;
DROP TRIGGER kg_sync_release ON servers;
DROP TRIGGER kg_sync ON environments;
DROP TRIGGER kg_sync_release ON environments;
DROP TRIGGER kg_sync ON services;
DROP TRIGGER kg_sync_release ON services;
DROP TRIGGER kg_sync ON repositories;
DROP TRIGGER kg_sync_release ON repositories;
DROP TRIGGER kg_sync ON service_deployments;
DROP TRIGGER kg_sync_release ON service_deployments;
DROP TRIGGER kg_sync ON service_dependencies;
DROP TRIGGER kg_sync ON service_repositories;
DROP TRIGGER kg_sync ON environment_servers;
DROP TRIGGER kg_sync ON environment_services;

DELETE FROM kg_edges WHERE synced OR type = 'includes';
-- Linked nodes with nothing hand-made attached go (deployments before their servers); the rest are unlinked.
DO $$
DECLARE removed integer;
BEGIN
    LOOP
        DELETE FROM kg_nodes n
            WHERE num_nonnulls(n.server_id, n.deployment_id, n.environment_id, n.service_id, n.repository_id) = 1
              AND NOT EXISTS (SELECT 1 FROM kg_nodes c WHERE c.parent_id = n.id)
              AND NOT EXISTS (SELECT 1 FROM kg_node_documents d WHERE d.node_id = n.id);
        GET DIAGNOSTICS removed = ROW_COUNT;
        EXIT WHEN removed = 0;
    END LOOP;
END $$;

DROP FUNCTION kg_sync_relation();
DROP FUNCTION kg_sync_inventory();
DROP FUNCTION kg_sync_release(text, uuid, text);
DROP FUNCTION kg_sync_row(text, uuid);
DROP FUNCTION kg_unsync_edge(uuid, uuid, text);
DROP FUNCTION kg_sync_edge(uuid, uuid, uuid, text);
DROP FUNCTION kg_free_name(uuid, text, uuid, text, uuid);
DROP FUNCTION kg_node_for(text, uuid);

ALTER TABLE kg_edges
    DROP COLUMN synced,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores'));
ALTER TABLE kg_nodes
    DROP COLUMN server_id,
    DROP COLUMN deployment_id,
    DROP COLUMN environment_id,
    DROP COLUMN service_id,
    DROP COLUMN repository_id;

COMMIT;
