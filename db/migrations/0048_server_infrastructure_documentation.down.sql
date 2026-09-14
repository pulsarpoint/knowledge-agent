BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:infrastructure:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Server infrastructure architecture'
            OR p.description<>'Infrastructure, runtimes and operations on one server in an environment.' OR p.input_kind<>'server'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:infrastructure:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='04a7b6159f6999ba6a43f0e68ba598bb70ddf29b499ac168adc38b613a8a1a6f') THEN
            RAISE EXCEPTION 'The server infrastructure prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_server_infrastructure_prompt ON projects;
DROP FUNCTION seed_server_infrastructure_prompt_on_project();
DROP FUNCTION seed_server_infrastructure_prompt(uuid);
COMMIT;
