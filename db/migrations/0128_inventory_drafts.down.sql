BEGIN;
SET search_path = ka, public;
-- Back to 0127: the drafts go (dependants first), then the columns.

DELETE FROM connections WHERE status = 'draft';
DELETE FROM service_relations WHERE status = 'draft';
DELETE FROM service_deployments WHERE status = 'draft';
DELETE FROM environment_servers WHERE status = 'draft';
DELETE FROM services WHERE status = 'draft';
DELETE FROM servers WHERE status = 'draft';
DELETE FROM environments WHERE status = 'draft';
ALTER TABLE environments DROP COLUMN status;
ALTER TABLE servers DROP COLUMN status;
ALTER TABLE services DROP COLUMN status;
ALTER TABLE service_deployments DROP COLUMN status;
ALTER TABLE connections DROP COLUMN status;
ALTER TABLE service_relations DROP COLUMN status;
ALTER TABLE environment_servers DROP COLUMN status;
COMMIT;
