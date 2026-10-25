BEGIN;
SET search_path = ka, public;

-- Skills leave the access model: every run gets every enabled skill of the project (pinned in its agent_config as
-- skills). Stored access loses its skill entries: saved agents, runs, and the access copy in a run's agent_config.
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

COMMIT;
