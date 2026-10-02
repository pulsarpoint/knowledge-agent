BEGIN;
SET search_path=ka,public;
-- Do not discard administrator edits or detach a skill from an agent on downgrade.
DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_skills s
        WHERE s.id=md5('knowledge-agent-v3:skill:api-analysis:' || s.project_id::text)::uuid
        AND (s.revision<>1 OR NOT s.enabled OR s.name<>'api-analysis'
            OR s.description<>$description$Use when documenting or investigating an API endpoint from source and a code graph, especially when validation, middleware, database effects, and error paths must be explained accurately.$description$
            OR encode(sha256(convert_to(s.instructions,'UTF8')),'hex')<>'e82192ceefd35f35a7dece36014f534127ea65f2c5399b49d73a90a2b390cd0c'
            OR EXISTS(SELECT 1 FROM skills_agents a WHERE a.skill_id=s.id)
            OR EXISTS(SELECT 1 FROM agent_config_skills a WHERE a.skill_id=s.id))) THEN
        RAISE EXCEPTION 'Cannot remove an edited or assigned API analysis skill';
    END IF;
END $guard$;
DROP TRIGGER seed_api_analysis_skill ON projects;
DROP FUNCTION seed_api_analysis_skill_on_project();
DROP FUNCTION seed_api_analysis_skill(uuid);
DELETE FROM agent_skills s
WHERE s.id=md5('knowledge-agent-v3:skill:api-analysis:' || s.project_id::text)::uuid;
DELETE FROM principals WHERE issuer='knowledge-agent' AND subject='api-analysis-skill-migration';
COMMIT;
