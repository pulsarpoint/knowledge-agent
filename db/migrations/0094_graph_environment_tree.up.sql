BEGIN;
SET search_path = ka, public;

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
