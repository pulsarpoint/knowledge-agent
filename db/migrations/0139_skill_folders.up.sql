BEGIN;
SET search_path = ka, public;

-- Skills are folders (SKILL.md and its files) on disk; the row keeps the metadata, where the files came from and the
-- current folder. Existing skills are test data: they go with the skill entries of stored access (saved agents, runs,
-- and the access copy in a run's agent_config), and the API analysis seed that wrote instructions stops.
DROP TRIGGER seed_api_analysis_skill ON projects;
DROP FUNCTION seed_api_analysis_skill_on_project();
DROP FUNCTION seed_api_analysis_skill(uuid);

CREATE OR REPLACE FUNCTION pg_temp.without_skills(resources jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(jsonb_agg(item ORDER BY position), '[]'::jsonb)
    FROM jsonb_array_elements(resources) WITH ORDINALITY AS x(item, position)
    WHERE item->>'type' IS DISTINCT FROM 'skill'
$$;
UPDATE agents SET access = jsonb_set(access, '{resources}', pg_temp.without_skills(access->'resources'))
WHERE access->'resources' @> '[{"type": "skill"}]';
UPDATE agent_sessions SET access = jsonb_set(access, '{resources}', pg_temp.without_skills(access->'resources'))
WHERE access->'resources' @> '[{"type": "skill"}]';
UPDATE agent_sessions
SET agent_config = jsonb_set(agent_config, '{access,resources}',
                             pg_temp.without_skills(agent_config->'access'->'resources'))
WHERE jsonb_typeof(agent_config->'access'->'resources') = 'array'
  AND agent_config->'access'->'resources' @> '[{"type": "skill"}]';

DELETE FROM agent_skills;
ALTER TABLE agent_skills
    DROP COLUMN instructions,
    ADD COLUMN source jsonb NOT NULL CHECK (source->>'kind' IN ('upload', 'git', 'editor')),
    ADD COLUMN folder text NOT NULL CHECK (folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$');

COMMENT ON TABLE agent_skills IS 'Skill library: each skill is a folder (SKILL.md and its files) on disk; name and description are read from SKILL.md.';
COMMENT ON COLUMN agent_skills.source IS 'Where the files came from: {kind: upload, file} / {kind: git, url, ref, path, commit} / {kind: editor}.';
COMMENT ON COLUMN agent_skills.folder IS 'The current folder, relative to the data folder: skills/<id>/<version>. An update writes a new folder, then switches.';

COMMIT;
