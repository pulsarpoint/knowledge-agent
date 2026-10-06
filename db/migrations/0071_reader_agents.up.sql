SET search_path = ka, public;

-- Any agent that meets the Reader rules can be published to Reader users; the single Reader assistant setting goes away.
ALTER TABLE agents ADD COLUMN reader_enabled boolean NOT NULL DEFAULT false;
UPDATE agents SET reader_enabled = true WHERE id IN (SELECT knowledge_agent_id FROM reader_settings);
DROP TABLE reader_settings;
