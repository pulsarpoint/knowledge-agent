BEGIN;
SET search_path = ka, public;
-- Connections and relations are lost (inventory_links, still present until 0126, keeps the links it had). Environments
-- created for free-text labels stay; deployments keep their environment_id.
DROP TABLE service_relations;
DROP TABLE connections;
ALTER TABLE service_deployments DROP COLUMN revision;
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_environment_fk;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_fk
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE SET NULL (environment_id);
ALTER TABLE service_deployments ALTER COLUMN environment_id DROP NOT NULL;
COMMENT ON COLUMN service_deployments.environment_id IS 'Selected environment; null only for legacy/unlinked deployment labels.';
COMMIT;
