BEGIN;
SET search_path=ka,public;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM environments) THEN
        RAISE EXCEPTION 'Environments exist; export or remove them before rolling back';
    END IF;
END $$;
DROP VIEW environment_assets;
DROP TABLE environment_services,environment_servers;
DROP TABLE environments;
COMMIT;
