BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Copies of a server inside an environment become hand-made nodes again; the next sync makes one node per server.
UPDATE kg_nodes SET server_id = NULL, environment_id = NULL WHERE server_id IS NOT NULL AND environment_id IS NOT NULL;
DROP INDEX kg_nodes_by_environment;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
DROP INDEX kg_nodes_by_server;
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
ALTER TABLE kg_nodes
    DROP CONSTRAINT kg_nodes_one_source,
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1);

COMMIT;
