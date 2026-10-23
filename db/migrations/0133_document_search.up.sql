BEGIN;
SET search_path = ka, public;

-- search_documents: full-text search over live documents, title weighted above description above content. The tool's
-- query uses this exact expression (capabilities/document_tools.py SEARCH_VECTOR) so the index serves it. 'english':
-- stemmed, so "rotate" finds "rotation".
CREATE INDEX documents_search ON documents USING gin ((
    setweight(to_tsvector('english', coalesce(title, '')), 'A')
    || setweight(to_tsvector('english', coalesce(description, '')), 'B')
    || setweight(to_tsvector('english', coalesce(markdown, '')), 'C')
)) WHERE NOT is_draft;

-- The "Answer only from documents" mode is gone with the RAG reading tools.
ALTER TABLE agents DROP COLUMN documents_grounded;

COMMIT;
