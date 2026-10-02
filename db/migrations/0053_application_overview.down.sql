BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:overview:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Application overview' OR p.input_kind<>'code'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:overview:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='f756f5e5748fa0ba217e733f790c8bc264824dcc985aae14e3b87f96db99ad7c') THEN
            RAISE EXCEPTION 'The application overview prompt has been customized; preserve it before rollback';
        END IF;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_application_overview ON projects;
DROP FUNCTION seed_application_overview_on_project();
DROP FUNCTION seed_application_overview_prompt(uuid);
COMMIT;
