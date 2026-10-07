SET search_path = ka, public;

-- Claude Code agents are configured like native agents; only the runtime differs (0070 kept them as registrations).
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_a_registration;
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_not_the_coordinator CHECK (runtime = 'openai' OR NOT is_coordinator);
-- Claude Code's own tools over the working folders; with all four off the agent has only catalog tools.
ALTER TABLE agents ADD COLUMN claude_code_tools text[] NOT NULL DEFAULT '{Read,Grep,Glob,Bash}'
    CONSTRAINT claude_code_tools_known CHECK (claude_code_tools <@ ARRAY['Read','Grep','Glob','Bash']::text[]);

-- Every agent has its own system prompt again. Registrations get the built-in documentation prompt their sessions
-- used so far, as published version 1.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents DROP COLUMN prompt_agent;
WITH added AS MATERIALIZED (
    SELECT a.id AS agent, gen_random_uuid() AS version_id FROM agents a
    WHERE a.runtime = 'claude_code' AND NOT EXISTS (SELECT 1 FROM agent_prompts p WHERE p.agent = a.id)),
prompts AS (INSERT INTO agent_prompts(agent, active_version_id, next_version) SELECT agent, version_id, 2 FROM added)
INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at)
SELECT version_id, agent, 1, 'Built-in documentation writer',
    'You are an expert technical writer. You explore the provided folders and context to write accurate documentation; everything you state must be grounded in what you read.',
    now() FROM added;
-- Check the new prompt rows now, so no deferred event is pending when the FK below is added.
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey IMMEDIATE;
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey DEFERRED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
