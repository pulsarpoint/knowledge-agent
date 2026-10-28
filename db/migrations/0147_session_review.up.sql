BEGIN;
SET search_path = ka, public;

-- Session review: a reviewer chat on a finished session of a saved agent (purpose 'review'; its pinned config names
-- the reviewed run key, agent and revision). A reviewer creates skills disabled with source {kind: review}. The
-- single-shot tuning suggestions go (test data).
DROP TABLE agent_session_reviews;
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose
        CHECK (purpose IN ('documents', 'event', 'resource', 'revision', 'chat', 'review'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Write with an agent conversation (started from a resource or from the resources a person picked); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent); review: a reviewer chat on a finished chat or event run of the saved agent (agent_config.review_of: its run key).';
ALTER TABLE agent_skills
    DROP CONSTRAINT agent_skills_source_check,
    ADD CONSTRAINT agent_skills_source_check CHECK (source->>'kind' IN ('upload', 'git', 'editor', 'review'));
COMMENT ON COLUMN agent_skills.source IS 'Where the files came from: {kind: upload, file} / {kind: git, url, ref, path, commit} / {kind: editor} / {kind: review, session_id} (created disabled by a session reviewer).';

COMMIT;
