BEGIN;
SET search_path = ka, public;

-- 0140 down: remove the seeded "Code project assistant" and "Code project overview" prompts where they are still as
-- seeded (their one version, never saved again); a changed one stays. A new workspace no longer gets them.
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DELETE FROM documentation_prompts p
WHERE p.id IN (md5('knowledge-agent-v3:prompts:code-project-assistant:' || p.project_id::text)::uuid,
               md5('knowledge-agent-v3:prompts:code-project-overview:' || p.project_id::text)::uuid)
    AND p.revision = 1
    AND p.next_version = 2
    AND (SELECT count(*) FROM documentation_prompt_versions v WHERE v.prompt_id = p.id) = 1;
DROP TRIGGER seed_code_project_prompts ON projects;
DROP FUNCTION seed_code_project_prompts_on_project();
DROP FUNCTION seed_code_project_prompts(uuid);

COMMIT;
