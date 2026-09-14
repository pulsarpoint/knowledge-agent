BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:environment:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Environment architecture'
            OR p.description<>'Architecture of all components in one environment.' OR p.input_kind<>'mixed'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:environment:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='29ae8489a9040505b08efef5e90f2665c37e53b2fa8a5f80755e8cbaf4d74426') THEN
            RAISE EXCEPTION 'The environment documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:diagram:' || project_id::text)::uuid
        AND EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:diagram:v2:' || project_id::text)::uuid) LOOP
        IF p.revision<>2 OR p.next_version<>3 OR NOT p.enabled
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:diagram:v2:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>2
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='6f735934997e1a60fba38d3daf39bc812e611da09578e100c249f44cb15f228a') THEN
            RAISE EXCEPTION 'The diagram prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=md5('knowledge-agent-v3:documentation:diagram:v1:' || p.project_id::text)::uuid,
            revision=1,next_version=2 WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:diagram:v2:' || p.project_id::text)::uuid;
    END LOOP;
END $$;
DROP TRIGGER seed_environment_documentation_prompt ON projects;
DROP FUNCTION seed_environment_documentation_prompt_on_project();
DROP FUNCTION seed_environment_documentation_prompt(uuid);
COMMIT;
