BEGIN;
ALTER TABLE ka.agent_prompt_versions ALTER CONSTRAINT agent_prompt_versions_agent_fkey NOT DEFERRABLE;
CREATE OR REPLACE VIEW ka.llm_assignments AS
SELECT r.singleton,s.model_id AS search_model_id,c.model_id AS code_model_id,
       o.model_id AS operations_model_id,r.revision,r.updated_by,r.updated_at
FROM ka.llm_assignment_revision r CROSS JOIN ka.agents s CROSS JOIN ka.agents c CROSS JOIN ka.agents o
WHERE s.id='search' AND c.id='code' AND o.id='operations';
COMMIT;
