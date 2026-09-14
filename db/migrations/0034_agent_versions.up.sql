BEGIN;
CREATE TABLE ka.agent_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES ka.agents(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    label text NOT NULL CHECK (length(label) BETWEEN 1 AND 160),
    notes text NOT NULL DEFAULT '',
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    created_by uuid NOT NULL REFERENCES ka.principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    source_review_id uuid REFERENCES ka.agent_session_reviews(id),
    UNIQUE (agent, version),
    UNIQUE (id, agent)
);
ALTER TABLE ka.agents ADD COLUMN active_config_version_id uuid;
ALTER TABLE ka.agents ADD COLUMN next_config_version integer NOT NULL DEFAULT 1 CHECK (next_config_version > 0);
ALTER TABLE ka.agents ADD CONSTRAINT agent_active_config_version
    FOREIGN KEY (active_config_version_id,id) REFERENCES ka.agent_versions(id,agent) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE ka.agent_test_sessions ADD COLUMN agent_version_id uuid;
ALTER TABLE ka.agent_test_sessions ADD CONSTRAINT session_agent_version
    FOREIGN KEY (agent_version_id,agent) REFERENCES ka.agent_versions(id,agent);
CREATE INDEX agent_versions_created ON ka.agent_versions(agent,created_at DESC);
COMMIT;
