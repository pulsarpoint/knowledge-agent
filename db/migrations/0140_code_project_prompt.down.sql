BEGIN;
SET search_path = ka, public;

-- 0140 down: remove the seeded "Code project overview" prompt where it is still as seeded (its one version, never
-- saved again); a changed one stays. A new workspace no longer gets it.
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DELETE FROM documentation_prompts p
WHERE p.id = md5('knowledge-agent-v3:prompts:code-project-overview:' || p.project_id::text)::uuid
    AND p.revision = 1
    AND p.next_version = 2
    AND (SELECT count(*) FROM documentation_prompt_versions v WHERE v.prompt_id = p.id) = 1;
DROP TRIGGER seed_code_project_overview ON projects;
DROP FUNCTION seed_code_project_overview_on_project();
DROP FUNCTION seed_code_project_overview_prompt(uuid);

COMMIT;
