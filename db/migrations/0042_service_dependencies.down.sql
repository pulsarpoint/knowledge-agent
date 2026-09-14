BEGIN;
SET search_path=ka,public;
LOCK TABLE service_dependencies IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM service_dependencies) THEN
        RAISE EXCEPTION 'Service dependencies exist; export/migrate them before rollback';
    END IF;
END $$;
DROP TABLE service_dependencies;
COMMIT;
