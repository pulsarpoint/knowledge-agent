BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- An agent's system prompt is one text field: what runs. The active prompt version's text moves into the agent;
-- prompt rows are no longer required (0080 drops them with the configuration versions).
ALTER TABLE agents ADD COLUMN system_prompt text NOT NULL DEFAULT ''
    CONSTRAINT system_prompt_length CHECK (char_length(system_prompt) <= 100000);
UPDATE agents a SET system_prompt = v.content
FROM agent_prompts p JOIN agent_prompt_versions v ON v.agent = p.agent AND v.id = p.active_version_id
WHERE p.agent = a.id;
-- The default only fills existing rows: every new agent names its prompt.
ALTER TABLE agents ALTER COLUMN system_prompt DROP DEFAULT;
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
COMMENT ON COLUMN agents.system_prompt IS 'The agent''s system prompt, edited on its General tab. What runs.';

COMMIT;
