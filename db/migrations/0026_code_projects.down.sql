BEGIN;
-- Refuse a downgrade that would discard local projects or their configuration.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM ka.repositories WHERE remote_url IS NULL OR clone_path IS NOT NULL OR sync_interval_minutes IS NOT NULL)
    THEN RAISE EXCEPTION 'Remove local projects and clear clone/pull settings before downgrading code projects'; END IF;
END $$;
DROP TABLE ka.code_project_folders;
ALTER TABLE ka.repositories DROP COLUMN description, DROP COLUMN clone_path,
    DROP COLUMN sync_interval_minutes, DROP COLUMN next_sync_at, DROP COLUMN revision,
    ALTER COLUMN remote_url SET NOT NULL;
COMMIT;
