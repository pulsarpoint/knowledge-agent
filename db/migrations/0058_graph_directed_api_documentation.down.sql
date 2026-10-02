BEGIN;
SET search_path=ka,public;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions v ON v.prompt_id=d.id
        WHERE v.id=md5('knowledge-agent-v3:documentation:api:v3:' || d.project_id::text)::uuid LOOP
        IF p.revision<>1 OR p.next_version<>4 OR p.active_version_id IS DISTINCT FROM md5('knowledge-agent-v3:documentation:api:v3:' || p.project_id::text)::uuid
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=p.id)<>3
            OR NOT EXISTS(SELECT 1 FROM documentation_prompt_versions WHERE id=p.active_version_id
                AND encode(sha256(convert_to(content,'UTF8')),'hex')='0c00c5f5d860cddf809106b4b1855f0aedaf6623fd26a396fd69fa7584401b3d') THEN
            RAISE EXCEPTION 'The graph-directed API documentation prompt has been customized; preserve it before rollback';
        END IF;
        UPDATE documentation_workflows w SET drafts=jsonb_set(w.drafts,'{api,content}',to_jsonb(old.content)),updated_at=now()
        FROM documentation_prompt_versions old,documentation_prompt_versions current
        WHERE w.project_id=p.project_id AND current.id=p.active_version_id
        AND old.id=md5('knowledge-agent-v3:documentation:api:v2:' || p.project_id::text)::uuid
        AND w.drafts#>>'{api,content}'=current.content;
        UPDATE documentation_prompts SET active_version_id=md5('knowledge-agent-v3:documentation:api:v2:' || p.project_id::text)::uuid,next_version=3 WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=p.active_version_id;
    END LOOP;
END $$;
DROP FUNCTION seed_api_documentation_prompt(uuid);
ALTER FUNCTION seed_api_documentation_prompt_v2(uuid) RENAME TO seed_api_documentation_prompt;
COMMIT;
