BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE agents ALTER COLUMN system_prompt SET DEFAULT '';
-- 0078 reads the prompt from the active prompt version. A prompt edited after 0079 becomes a new active version;
-- an agent without prompt rows (created after 0079, or after 0080 down) gets its prompt as published version 1.
-- A version needs text (an empty prompt becomes a placeholder) and at most 40 000 characters.
WITH changed AS MATERIALIZED (
    SELECT p.agent, gen_random_uuid() AS version_id, p.next_version AS version, left(a.system_prompt, 40000) AS content
    FROM agents a JOIN agent_prompts p ON p.agent = a.id JOIN agent_prompt_versions v ON v.id = p.active_version_id
    WHERE btrim(a.system_prompt) <> '' AND v.content <> a.system_prompt),
versions AS (INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
    SELECT version_id, agent, version, 'System prompt', content, now() FROM changed)
UPDATE agent_prompts p SET active_version_id = c.version_id, next_version = c.version + 1, revision = p.revision + 1
FROM changed c WHERE p.agent = c.agent;
WITH added AS MATERIALIZED (
    SELECT a.id AS agent, gen_random_uuid() AS version_id,
        coalesce(nullif(btrim(left(a.system_prompt, 40000)), ''), 'Follow the administrator''s instructions.') AS content
    FROM agents a WHERE NOT EXISTS (SELECT 1 FROM agent_prompts p WHERE p.agent = a.id)),
prompts AS (INSERT INTO agent_prompts(agent, active_version_id, next_version) SELECT agent, version_id, 2 FROM added)
INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
SELECT version_id, agent, 1, 'System prompt', content, now() FROM added;
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agents DROP COLUMN system_prompt;

COMMIT;
