SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents WHERE runtime = 'claude_code') THEN
        RAISE EXCEPTION 'Claude Code agents exist; delete their sessions and agents before rolling back 0070';
    END IF;
END $$;

DROP TABLE agent_session_inputs;
DROP TABLE agent_sessions_cc;
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents DROP COLUMN prompt_agent;
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_a_registration;
ALTER TABLE agents DROP COLUMN runtime;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
