BEGIN;
SET search_path = ka, public;
-- Back to 0128: the shape only (service proposals and their links are not restored).

ALTER TABLE discovery_proposals
    DROP CONSTRAINT discovery_proposals_kind_check,
    ADD COLUMN service_proposal_id uuid REFERENCES discovery_proposals(id) ON DELETE CASCADE,
    ADD CONSTRAINT discovery_proposals_kind_check CHECK (kind IN ('service', 'document')),
    ADD CONSTRAINT discovery_proposals_document_fields CHECK (kind = 'document' OR (service_proposal_id IS NULL AND document_id IS NULL)),
    ADD CONSTRAINT discovery_proposals_one_service CHECK (service_id IS NULL OR service_proposal_id IS NULL);
CREATE INDEX discovery_proposals_service_proposal ON discovery_proposals(service_proposal_id) WHERE service_proposal_id IS NOT NULL;
COMMENT ON TABLE discovery_proposals IS
    'What a discovery run proposes for the inventory: a service (service_id: the existing service it updates) or a document (service_id or service_proposal_id: the service it is about, else the server; document_id: the document it becomes a new version of). Accepted or rejected by an administrator.';

COMMIT;
