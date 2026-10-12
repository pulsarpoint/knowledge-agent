BEGIN;
SET search_path = ka, public;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM document_drafts WHERE status = 'open') OR EXISTS (SELECT 1 FROM document_sources)
       OR EXISTS (SELECT 1 FROM document_links) THEN
        RAISE EXCEPTION 'Open document drafts, document sources or document links exist and would be lost; promote or discard the drafts and remove the links before rollback';
    END IF;
END $$;
DROP TABLE document_links;
DROP TABLE document_sources;
DROP TABLE document_drafts;
DROP FUNCTION freeze_decided_document_draft();

COMMIT;
