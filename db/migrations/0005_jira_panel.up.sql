SET search_path = ka, public;

ALTER TABLE jira_connections
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    ADD COLUMN fetch_revision integer NOT NULL DEFAULT 0 CHECK (fetch_revision >= 0),
    ADD COLUMN last_fetch_limit integer CHECK (last_fetch_limit BETWEEN 1 AND 200),
    ADD COLUMN last_fetch_total integer CHECK (last_fetch_total >= 0);

COMMENT ON COLUMN jira_connections.fetch_revision IS 'Reject concurrent fetches that would overwrite a newer ticket snapshot.';
COMMENT ON COLUMN jira_tickets.out_of_scope_at IS 'Tickets outside the latest fetched selection are retained but hidden from the current list.';
