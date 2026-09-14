BEGIN;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM ka.agent_board_tasks WHERE agent_id IS NULL) THEN
        RAISE EXCEPTION 'Coordinator run history requires migration 30; export and remove those runs before downgrading';
    END IF;
END $$;
ALTER TABLE ka.agent_board_tasks DROP CONSTRAINT board_subagent_identity;
ALTER TABLE ka.agent_board_tasks ALTER COLUMN agent_id SET NOT NULL;
DROP TABLE ka.coordinator_settings;
COMMIT;
