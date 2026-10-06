SET search_path = ka, public;

CREATE TABLE reader_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    knowledge_agent_id text NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO reader_settings(knowledge_agent_id)
    SELECT id FROM agents WHERE reader_enabled ORDER BY lower(name), id LIMIT 1;
ALTER TABLE agents DROP COLUMN reader_enabled;
