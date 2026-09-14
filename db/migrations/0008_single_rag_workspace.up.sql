SET search_path = ka, public;

-- One source mount belongs to one workspace. Retain existing IDs and foreign
-- keys without allowing a second project to share or process the same files.
CREATE UNIQUE INDEX rag_workspaces_singleton ON rag_workspaces ((true));
