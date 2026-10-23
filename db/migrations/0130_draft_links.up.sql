BEGIN;
SET search_path = ka, public;

-- An agent's draft of a live document may hold links ('linked') to servers, services and environments: update_document
-- stores them with the draft, and promoting the draft adds them to the document (documents/store.py promote_draft).
-- What the document was built for stays the document's own: a draft of a live document never holds a built_for link.
CREATE OR REPLACE FUNCTION document_resources_owner() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.connection = 'built_for' AND EXISTS (SELECT 1 FROM documents WHERE id = NEW.document_id AND draft_of IS NOT NULL) THEN
        RAISE EXCEPTION 'Built-for links belong to the document, not to a draft of it';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER document_resources_owner ON document_resources;
CREATE TRIGGER document_resources_owner BEFORE INSERT OR UPDATE OF document_id, connection ON document_resources
    FOR EACH ROW EXECUTE FUNCTION document_resources_owner();

COMMIT;
