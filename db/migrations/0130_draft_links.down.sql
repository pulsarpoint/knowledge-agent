BEGIN;
SET search_path = ka, public;

-- Links belong to the document again: the links drafts of live documents hold are dropped (they were not published).
DELETE FROM document_resources r USING documents d WHERE d.id = r.document_id AND d.draft_of IS NOT NULL;
CREATE OR REPLACE FUNCTION document_resources_owner() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents WHERE id = NEW.document_id AND draft_of IS NOT NULL) THEN
        RAISE EXCEPTION 'Links belong to the document, not to a draft of it';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER document_resources_owner ON document_resources;
CREATE TRIGGER document_resources_owner BEFORE INSERT OR UPDATE OF document_id ON document_resources
    FOR EACH ROW EXECUTE FUNCTION document_resources_owner();

COMMIT;
