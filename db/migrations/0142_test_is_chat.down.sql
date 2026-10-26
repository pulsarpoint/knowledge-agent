BEGIN;
SET search_path = ka, public;

-- Back to 0141: the purpose check allows 'test' again. Chats stay chats and their run records keep chat_session (test
-- data; 0122's Run agent chats were chats too).
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose
        CHECK (purpose IN ('documents', 'test', 'event', 'resource', 'revision', 'chat'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent run from the Agents page (Run agent).';

COMMIT;
