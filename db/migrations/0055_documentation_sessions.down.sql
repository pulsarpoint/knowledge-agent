BEGIN;
SET search_path=ka,public;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM documentation_workflows) THEN
        RAISE EXCEPTION 'Saved documentation workflows exist; preserve them before rollback';
    END IF;
END $$;
DROP VIEW documentation_workflow_details;
DROP TABLE documentation_workflow_jobs;
DROP TABLE documentation_workflows;
COMMIT;
