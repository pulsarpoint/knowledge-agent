BEGIN;
SET search_path = ka, public;

-- Accepting a discovery proposal for a document creates a draft (a brand-new draft, or a draft of the live document it
-- updates) instead of writing live content; the proposal records it. Not covered by the live-document trigger: it
-- names a draft on purpose. Publishing a brand-new draft attaches it to the proposal's inventory entry.
ALTER TABLE discovery_proposals ADD COLUMN draft_id uuid REFERENCES documents(id) ON DELETE SET NULL;
CREATE INDEX discovery_proposals_draft ON discovery_proposals(draft_id) WHERE draft_id IS NOT NULL;

COMMIT;
