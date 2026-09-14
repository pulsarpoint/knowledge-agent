BEGIN;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM ka.coordinator_settings s JOIN ka.agents a ON a.id=s.agent_id
        WHERE s.notes<>'' OR a.revision>1
        OR (SELECT count(*) FROM ka.agent_prompt_versions v WHERE v.agent=a.id)>1
        OR EXISTS (SELECT 1 FROM ka.agent_board_tasks t WHERE t.agent_id=a.id)) THEN
        RAISE EXCEPTION 'Coordinator edits or history require migration 31; restore a backup to downgrade';
    END IF;
END $$;
ALTER TABLE ka.coordinator_settings
    ADD COLUMN model_id uuid REFERENCES ka.llm_models(id),
    ADD COLUMN system_prompt text;
UPDATE ka.coordinator_settings s SET model_id=a.model_id,system_prompt=v.content
FROM ka.agents a JOIN ka.agent_prompts p ON p.agent=a.id
JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE a.id=s.agent_id;
ALTER TABLE ka.coordinator_settings ALTER COLUMN system_prompt SET NOT NULL;
ALTER TABLE ka.coordinator_settings ADD CHECK (length(btrim(system_prompt)) BETWEEN 1 AND 40000);
DROP TRIGGER protect_coordinator_agent ON ka.agents;
DROP FUNCTION ka.protect_coordinator_agent();
ALTER TABLE ka.coordinator_settings DROP COLUMN agent_id, DROP COLUMN notes;
DELETE FROM ka.agent_prompts WHERE agent IN (SELECT id FROM ka.agents WHERE is_coordinator);
DELETE FROM ka.agent_prompt_versions WHERE agent IN (SELECT id FROM ka.agents WHERE is_coordinator);
DELETE FROM ka.agents WHERE is_coordinator;
ALTER TABLE ka.agents DROP CONSTRAINT coordinator_delegates_resources;
DROP INDEX ka.one_coordinator_agent;
ALTER TABLE ka.agents DROP COLUMN is_coordinator;
COMMIT;
