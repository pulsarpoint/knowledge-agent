BEGIN;
SET search_path = ka, public;

-- Preserve existing IDs and agent/service/job links while allowing local projects.
ALTER TABLE repositories
    ALTER COLUMN remote_url DROP NOT NULL,
    ADD COLUMN description text NOT NULL DEFAULT '',
    ADD COLUMN clone_path relative_path,
    ADD COLUMN sync_interval_minutes integer CHECK (sync_interval_minutes BETWEEN 5 AND 10080),
    ADD COLUMN next_sync_at timestamptz,
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    ADD CHECK (remote_url IS NOT NULL OR (clone_path IS NULL AND sync_interval_minutes IS NULL AND credential_id IS NULL));

CREATE TABLE code_project_folders (
    code_project_id uuid NOT NULL,
    project_id uuid NOT NULL,
    path relative_path NOT NULL,
    PRIMARY KEY (code_project_id, path),
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE
);
INSERT INTO code_project_folders(code_project_id,project_id,path)
    SELECT id,project_id,checkout_path FROM repositories WHERE checkout_path IS NOT NULL;
CREATE UNIQUE INDEX code_projects_clone_path ON repositories(clone_path)
    WHERE clone_path IS NOT NULL AND archived_at IS NULL;
CREATE INDEX code_projects_due ON repositories(next_sync_at)
    WHERE archived_at IS NULL AND sync_interval_minutes IS NOT NULL;
COMMENT ON TABLE repositories IS 'Code projects: one or more folders inside CODE_ROOT, optionally with a Git source. Historical table name preserves existing foreign keys and API compatibility.';
COMMENT ON TABLE code_project_folders IS 'Folders relative to CODE_ROOT belonging to each code project. Agent and service selections reference the project ID.';
COMMIT;
