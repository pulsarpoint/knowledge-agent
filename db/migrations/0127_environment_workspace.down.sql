BEGIN;
SET search_path = ka, public;
-- Back to 0126: membership through deployments only, no service address, connections to a deployment or a host.
-- Lost: empty memberships and connections to external services.

DELETE FROM connections WHERE to_service_id IS NOT NULL;
ALTER TABLE connections DROP CONSTRAINT connections_once,
    ADD CONSTRAINT connections_once UNIQUE NULLS NOT DISTINCT (from_deployment_id, to_deployment_id, to_host, port, protocol);
ALTER TABLE connections DROP CONSTRAINT connections_one_target,
    ADD CONSTRAINT connections_one_target CHECK (num_nonnulls(to_deployment_id, to_host) = 1);
ALTER TABLE connections DROP COLUMN to_service_id;  -- its foreign key and index go with it
COMMENT ON TABLE connections IS 'Network connections: a deployment connects to another deployment or to an external host, on a port with a protocol. May cross environments.';
ALTER TABLE services DROP COLUMN address;
DROP TABLE environment_servers;
COMMENT ON TABLE environments IS 'Named architecture environments. A server or service is in an environment when one of its deployments is.';
COMMIT;
