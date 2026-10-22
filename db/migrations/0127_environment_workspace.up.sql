BEGIN;
SET search_path = ka, public;

-- A server is put in an environment on its own (it used to be in one only through a deployment); services are still
-- placed only by deployments. Backfilled from the deployments.
CREATE TABLE environment_servers (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    server_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (environment_id, server_id),
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE
);
CREATE INDEX environment_servers_server ON environment_servers (server_id);
INSERT INTO environment_servers (project_id, environment_id, server_id)
SELECT DISTINCT project_id, environment_id, server_id FROM service_deployments;
COMMENT ON TABLE environment_servers IS 'The servers in an environment; a deployment''s server is always a member.';
COMMENT ON TABLE environments IS 'Named architecture environments. A server is in an environment through ka.environment_servers; a service when one of its deployments is.';

-- An external system is a service of type external, reached at its address; connections can point to it.
ALTER TABLE services ADD COLUMN address text
    CONSTRAINT services_address CHECK (address ~ '^[A-Za-z0-9.:-]{1,253}$');
COMMENT ON COLUMN services.address IS 'Where an external service is reached: an IP address or hostname.';
ALTER TABLE connections ADD COLUMN to_service_id uuid;
ALTER TABLE connections ADD FOREIGN KEY (to_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE;
CREATE INDEX connections_to_service ON connections (to_service_id);
ALTER TABLE connections DROP CONSTRAINT connections_one_target,
    ADD CONSTRAINT connections_one_target CHECK (num_nonnulls(to_deployment_id, to_host, to_service_id) = 1);
ALTER TABLE connections DROP CONSTRAINT connections_once,
    ADD CONSTRAINT connections_once
        UNIQUE NULLS NOT DISTINCT (from_deployment_id, to_deployment_id, to_host, to_service_id, port, protocol);
COMMENT ON COLUMN connections.to_service_id IS 'An external service (type external, checked by the store) this connection reaches.';
COMMENT ON TABLE connections IS 'Network connections: a deployment connects to another deployment, an external service or an external host, on a port with a protocol. May cross environments.';
COMMIT;
