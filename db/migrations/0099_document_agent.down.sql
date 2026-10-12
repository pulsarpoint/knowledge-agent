BEGIN;
SET search_path = ka, public;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions WHERE purpose = 'revision') THEN
        RAISE EXCEPTION 'Ask agent runs exist; delete them before rollback (their drafts stay)';
    END IF;
END $$;
DROP INDEX agent_sessions_revision;
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_revision_document,
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery')),
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery')),
    DROP CONSTRAINT agent_sessions_target_document,
    DROP COLUMN target_document_id;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id).';

COMMIT;
