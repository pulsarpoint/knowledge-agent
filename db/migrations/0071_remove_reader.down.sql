SET search_path = ka, public;

-- Restores the Reader schema from 0060; deleted Reader conversations are not restored.
ALTER TABLE agent_sessions
    ADD COLUMN hidden_by_owner_at timestamptz,
    ADD COLUMN channel text NOT NULL DEFAULT 'admin' CHECK (channel IN ('admin', 'reader'));
CREATE INDEX agent_sessions_reader_owner ON agent_sessions (actor_id, last_activity_at DESC)
    WHERE channel = 'reader';
CREATE TABLE reader_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    knowledge_agent_id text NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO reader_settings(knowledge_agent_id) SELECT id FROM agents WHERE id = 'search';
