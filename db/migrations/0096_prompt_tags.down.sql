BEGIN;
SET search_path = ka, public;

DROP TRIGGER documentation_prompt_default_tags ON documentation_prompts;
DROP FUNCTION documentation_prompt_default_tags();
DROP INDEX documentation_prompts_tags_gin;
ALTER TABLE documentation_prompts
    DROP CONSTRAINT documentation_prompts_tags,
    DROP COLUMN tags,
    ALTER COLUMN input_kind DROP DEFAULT;
DROP FUNCTION documentation_prompt_tags_unique(text[]);
COMMENT ON COLUMN documentation_prompts.input_kind IS NULL;

COMMIT;
