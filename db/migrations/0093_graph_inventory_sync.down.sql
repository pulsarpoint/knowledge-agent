BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;  -- settle pending link checks so the tables can be altered

DROP TRIGGER kg_graph_changed ON documents;
DROP TRIGGER kg_graph_changed ON kg_node_documents;
DROP TRIGGER kg_graph_changed ON kg_edges;
DROP TRIGGER kg_graph_changed ON kg_nodes;
DROP FUNCTION kg_graph_changed();
DROP TABLE kg_graph_version;

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
