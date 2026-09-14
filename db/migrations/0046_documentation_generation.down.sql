BEGIN;
SET search_path=ka,public;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM document_environments) OR EXISTS(SELECT 1 FROM documentation_generations) THEN
        RAISE EXCEPTION 'Keep migration 46 while environment documents or generation history exist';
    END IF;
END $$;
DROP TRIGGER seed_service_documentation_prompts ON projects;
DROP FUNCTION seed_service_documentation_prompts_on_project();
DROP FUNCTION seed_service_documentation_prompts(uuid);
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:code:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Code analysis and architecture'
            OR p.input_kind<>'code' OR p.description<>'Code analysis and architecture specialist for managed document generation.'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:code:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='345780b3f763ab485b6ff805fb5cd922fdcaa4f239ec110e230aeac69cc7405c') THEN
            RAISE EXCEPTION 'A documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:service:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Service purpose and environment connections'
            OR p.input_kind<>'service' OR p.description<>'Service purpose and environment connections specialist for managed document generation.'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:service:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='88ebac67e68ce8df2116d77d626d1c000792201a78a8c8647434be5e2d34b54b') THEN
            RAISE EXCEPTION 'A documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:deployment:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Deployment and service operations'
            OR p.input_kind<>'server' OR p.description<>'Deployment and service operations specialist for managed document generation.'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:deployment:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='6f365385489446616fe156807cd9ba112c50bd123243ef6c7b41a138267070ab') THEN
            RAISE EXCEPTION 'A documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts WHERE id=md5('knowledge-agent-v3:documentation:diagram:' || project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>2 OR NOT p.enabled OR p.name<>'Code component diagram (D2 / TALA)'
            OR p.input_kind<>'code' OR p.description<>'Code component diagram (D2 / TALA) specialist for managed document generation.'
            OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:diagram:v1:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>1
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id AND created_by IS NULL
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='68ce5875c06291b4e598cf4e1b7270a79201330960ae0e2508be234964a41a6f') THEN
            RAISE EXCEPTION 'A documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=p.id;
        DELETE FROM documentation_prompts WHERE id=p.id;
    END LOOP;
END $$;
DROP VIEW document_details;
CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;

DROP TABLE document_environments;
COMMIT;
