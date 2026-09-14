SET search_path TO ka, public;

-- Answer generation belongs to the search agent. Keep indexing settings only.
UPDATE rag_workspaces
SET settings = settings - 'chat_base_url' - 'chat_model' - 'answer_max_tokens';
ALTER TABLE rag_workspaces DROP COLUMN chat_secret;
