BEGIN;
SET search_path = ka, public;

-- Every inventory entry is a draft (written by an agent, waiting for a person) or confirmed. Existing rows are
-- confirmed.
ALTER TABLE environments ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT environments_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN environments.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE servers ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT servers_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN servers.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE services ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT services_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN services.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE service_deployments ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT service_deployments_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN service_deployments.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE connections ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT connections_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN connections.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE service_relations ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT service_relations_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN service_relations.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
ALTER TABLE environment_servers ADD COLUMN status text NOT NULL DEFAULT 'confirmed'
    CONSTRAINT environment_servers_status CHECK (status IN ('draft', 'confirmed'));
COMMENT ON COLUMN environment_servers.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
COMMIT;
