BEGIN;
SET search_path = ka, public;

-- Service proposals give way to inventory drafts (0128): an agent writes services, deployments, connections and
-- relations as drafts. Only document proposals stay. A service proposal goes with the document proposals that were
-- about it (service_proposal_id cascades); the column and its constraints go, since only service proposals used it.
DELETE FROM discovery_proposals WHERE kind = 'service';
DROP INDEX discovery_proposals_service_proposal;
ALTER TABLE discovery_proposals
    DROP CONSTRAINT discovery_proposals_document_fields,
    DROP CONSTRAINT discovery_proposals_one_service,
    DROP COLUMN service_proposal_id,
    DROP CONSTRAINT discovery_proposals_kind_check,
    ADD CONSTRAINT discovery_proposals_kind_check CHECK (kind = 'document');
COMMENT ON TABLE discovery_proposals IS
    'What a run proposes for a person to review: a document (service_id: the service it is about; document_id: the document it becomes a new version of). Inventory findings are drafts instead (status on the inventory tables). Accepted or rejected by an administrator.';

COMMIT;
