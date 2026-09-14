SET search_path = ka, public;

-- Agent identity and its first active prompt are saved in the same transaction.
-- The reciprocal FK makes the prompt mandatory for every persisted agent.
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk
    FOREIGN KEY (id) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
