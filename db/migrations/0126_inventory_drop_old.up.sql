BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;  -- settle the deferrable deployment identities before they are dropped

-- The old inventory structures, replaced in 0125 by deployments (with a required environment), connections and
-- service relations, and read through knowledge_admin/inventory (assets.py replaces the *_assets views).
DROP VIEW service_assets;
DROP VIEW environment_assets;
DROP VIEW server_assets;
DROP VIEW inventory_entries;
DROP TABLE inventory_links;
DROP TABLE environment_services;
DROP TABLE environment_servers;

-- The free-text environment label and its identity go; a deployment is identified by its environment_id. The identity
-- is no longer deferrable (only the old service form needed that).
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key;
ALTER TABLE service_deployments DROP COLUMN environment;
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_environment_identity;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_identity
    UNIQUE (service_id, server_id, environment_id, runtime_name);
COMMENT ON TABLE environments IS 'Named architecture environments. A server or service is in an environment when one of its deployments is.';
COMMIT;
