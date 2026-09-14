SET search_path = ka, public;

-- The explicit server allowlist controls agent access; no second enable flag is needed.
ALTER TABLE operations_settings DROP COLUMN enabled;
