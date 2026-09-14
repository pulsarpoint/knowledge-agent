BEGIN;
ALTER TABLE ka.agent_prompt_versions ALTER CONSTRAINT agent_prompt_versions_agent_fkey DEFERRABLE INITIALLY DEFERRED;
-- Removing one named seed agent must not make the model catalog disappear.
CREATE OR REPLACE VIEW ka.llm_assignments AS
SELECT r.singleton,s.model_id AS search_model_id,c.model_id AS code_model_id,
       o.model_id AS operations_model_id,r.revision,r.updated_by,r.updated_at
FROM ka.llm_assignment_revision r
LEFT JOIN ka.agents s ON s.id='search'
LEFT JOIN ka.agents c ON c.id='code'
LEFT JOIN ka.agents o ON o.id='operations';
COMMIT;
