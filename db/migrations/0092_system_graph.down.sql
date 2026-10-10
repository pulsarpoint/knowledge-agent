BEGIN;
SET search_path = ka, public;

DROP TABLE kg_node_documents;
DROP TABLE kg_edges;
DROP TABLE kg_nodes;
DROP FUNCTION kg_edges_check();
DROP FUNCTION kg_nodes_place();

COMMIT;
