BEGIN;
SET search_path = ka, public;

-- Ask agent on a document: an ad-hoc agent session (purpose revision) revises one document through the document tools
-- (get_doc, save_doc, edit_doc), which save the run's drafts. The run goes with its document.
ALTER TABLE agent_sessions
    ADD COLUMN target_document_id uuid,
    ADD CONSTRAINT agent_sessions_target_document FOREIGN KEY (target_document_id, project_id)
        REFERENCES documents(id, project_id) ON DELETE CASCADE,
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery', 'revision')),
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery', 'revision')),
    ADD CONSTRAINT agent_sessions_revision_document CHECK ((purpose = 'revision') = (target_document_id IS NOT NULL));
CREATE INDEX agent_sessions_revision ON agent_sessions(target_document_id, last_activity_at DESC)
    WHERE target_document_id IS NOT NULL;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id); revision: an Ask agent run on a document (target_document_id).';
COMMENT ON COLUMN agent_sessions.target_document_id IS 'The document an Ask agent run revises; set exactly when purpose is revision.';

COMMIT;
