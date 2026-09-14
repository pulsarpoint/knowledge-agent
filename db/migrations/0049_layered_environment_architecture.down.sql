BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:environment:' || project_id::text)::uuid
        AND EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:environment:v2:' || project_id::text)::uuid) LOOP
        IF p.revision<>2 OR p.next_version<>3 OR NOT p.enabled OR p.name<>'Environment architecture'
            OR p.description<>'Architecture of all components in one environment.' OR p.input_kind<>'mixed'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:environment:v2:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>2
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='b42d0beaa5d037d7c1e04920f5cf2be1405343d5203e51c9593f08ff33eb9ae1') THEN
            RAISE EXCEPTION 'The layered environment prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=md5('knowledge-agent-v3:documentation:environment:v1:' || p.project_id::text)::uuid,
            revision=1,next_version=2 WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:environment:v2:' || p.project_id::text)::uuid;
    END LOOP;
END $$;
DROP TRIGGER seed_layered_environment_prompt ON projects;
DROP FUNCTION seed_layered_environment_prompt_on_project();
DROP FUNCTION seed_layered_environment_prompt(uuid);
COMMIT;
