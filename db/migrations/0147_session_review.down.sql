BEGIN;
SET search_path = ka, public;

-- Back to 0146: review runs are deleted with their run records (inputs cascade), review skills become editor skills,
-- and the tuning suggestions table comes back empty (test data).
DELETE FROM agent_run_events e USING agent_sessions s
WHERE s.purpose = 'review' AND starts_with(e.run_key, 'cc:' || s.id || ':');
DELETE FROM agent_sessions WHERE purpose = 'review';
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose
        CHECK (purpose IN ('documents', 'event', 'resource', 'revision', 'chat'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Write with an agent conversation (started from a resource or from the resources a person picked); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent).';
UPDATE agent_skills SET source = '{"kind": "editor"}' WHERE source->>'kind' = 'review';
ALTER TABLE agent_skills
    DROP CONSTRAINT agent_skills_source_check,
    ADD CONSTRAINT agent_skills_source_check CHECK (source->>'kind' IN ('upload', 'git', 'editor'));
COMMENT ON COLUMN agent_skills.source IS 'Where the files came from: {kind: upload, file} / {kind: git, url, ref, path, commit} / {kind: editor}.';

CREATE TABLE agent_session_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    actor_id uuid NOT NULL REFERENCES principals(id),
    comment text NOT NULL CHECK (char_length(comment) BETWEEN 1 AND 20000),
    context jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object'),
    suggestion text NOT NULL,
    response_turn integer CHECK (response_turn > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_session_reviews_session ON agent_session_reviews(session_id, created_at);
COMMENT ON TABLE agent_session_reviews IS
    'Tuning suggestions for Test sessions: the feedback, the reviewer''s context (redacted) and its proposed texts.';

COMMIT;
