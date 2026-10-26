BEGIN;
SET search_path = ka, public;

-- One way to run a saved agent: its Test tab sessions become chats (the agent page's Chat tab, Run agent on the
-- Agents page). Their run records' kind follows (test_session → chat_session); 'test' leaves the purposes.
UPDATE agent_sessions SET purpose = 'chat' WHERE purpose = 'test';
UPDATE agent_run_events SET payload = jsonb_set(payload, '{kind}', '"chat_session"')
WHERE kind = 'start' AND payload->>'kind' = 'test_session';
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose
        CHECK (purpose IN ('documents', 'event', 'resource', 'revision', 'chat'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, folder, draft); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent).';

COMMIT;
