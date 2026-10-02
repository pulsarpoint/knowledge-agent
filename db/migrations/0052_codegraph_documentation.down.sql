BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:code:' || project_id::text)::uuid
        AND EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:code:v2:' || project_id::text)::uuid) LOOP
        IF p.revision<>2 OR p.next_version<>3 OR NOT p.enabled OR p.name<>'Code analysis and architecture' OR p.input_kind<>'code'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:code:v2:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>2
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='69a01fb06feadc2cbbfcb796a02886bb2858422a6e25847e72c1bc0fd516732f') THEN
            RAISE EXCEPTION 'The code analysis prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=md5('knowledge-agent-v3:documentation:code:v1:' || p.project_id::text)::uuid,revision=1,next_version=2 WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=md5('knowledge-agent-v3:documentation:code:v2:' || p.project_id::text)::uuid;
    END LOOP;
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:deployment_diagram:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Deployment diagram (D2 / TALA)' OR p.input_kind<>'server'
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='d1386522e2ccdfbe3c4425e77fe6eaa96b15a2fb4c5703894dcb9c28ae916b52') THEN
            RAISE EXCEPTION 'The deployment diagram prompt has been customized; preserve it before rollback';
        END IF;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_service_graph_prompts ON projects;
DROP FUNCTION seed_codegraph_documentation_prompts_on_project();
DROP FUNCTION seed_codegraph_documentation_prompts(uuid);
COMMIT;
