BEGIN;
SET search_path = ka, public;

-- Chat sessions become Test sessions (they are a saved agent's conversations, as those are).
UPDATE agent_sessions SET purpose = 'test' WHERE purpose = 'chat';
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'resource', 'revision'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document.';

COMMIT;
