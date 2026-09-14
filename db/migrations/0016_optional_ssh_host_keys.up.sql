-- Connection readiness depends on login credentials. Blank host keys disable verification.
-- Keep the independent SSH access flag and the agent allowlist unchanged.
UPDATE ka.servers SET enabled=true, revision=revision+1, last_tested_at=NULL, last_test_error=NULL
WHERE NOT enabled AND auth_kind='ssh_private_key' AND credential_id IS NOT NULL
    AND username IS NOT NULL AND btrim(coalesce(known_hosts,''), E' \t\r\n')='';
