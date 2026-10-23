\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned
BEGIN;
SET search_path = ka, public;

CREATE FUNCTION pg_temp.assert_true(ok boolean, label text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    IF ok IS DISTINCT FROM true THEN RAISE EXCEPTION 'FAIL: %', label; END IF;
    RETURN 'PASS: ' || label;
END;
$$;
CREATE FUNCTION pg_temp.expect_failure(statement text, expected_state text, label text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE actual_state text;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS actual_state = RETURNED_SQLSTATE;
        IF actual_state <> expected_state THEN RAISE; END IF;
        RETURN 'PASS: ' || label;
    END;
    RAISE EXCEPTION 'FAIL: % (statement unexpectedly succeeded)', label;
END;
$$;

INSERT INTO principals(issuer, subject, email) VALUES ('https://sso.example/realms/docs', 'subject-1', 'admin@example.test') RETURNING id AS actor \gset
INSERT INTO projects(slug, name, created_by) VALUES ('alpha', 'Alpha', :'actor') RETURNING id AS project \gset
INSERT INTO projects(slug, name) VALUES ('beta', 'Beta') RETURNING id AS other_project \gset
INSERT INTO project_members VALUES (:'project', :'actor', 'admin', now());
INSERT INTO repositories(project_id, slug, name, remote_url)
VALUES (:'project', 'api', 'API', 'https://github.com/example/api.git') RETURNING id AS repo \gset
INSERT INTO repositories(project_id, slug, name, remote_url)
VALUES (:'other_project', 'api', 'Other API', 'git@example.test:api.git') RETURNING id AS other_repo \gset
INSERT INTO document_sets(project_id, slug, name) VALUES (:'project', 'docs', 'Docs') RETURNING id AS docs \gset
INSERT INTO document_sets(project_id, slug, name) VALUES (:'other_project', 'docs', 'Docs') RETURNING id AS other_docs \gset
INSERT INTO document_set_repositories VALUES (:'project', :'docs', :'repo');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO document_set_repositories VALUES (%L, %L, %L)', :'project', :'docs', :'other_repo'
), '23503', 'repository associations cannot cross projects');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO repositories(project_id,slug,name,remote_url) VALUES (%L,%L,%L,%L)',
    :'project', 'credential-in-url', 'Bad', 'https://token@github.com/a/b.git'
), '23514', 'HTTPS repository URLs cannot embed credentials');

INSERT INTO credentials(project_id, name, kind, ciphertext, key_version)
VALUES (:'project', 'Jira PAT', 'jira_pat', decode('010203', 'hex'), 'test-key') RETURNING id AS jira_credential \gset
INSERT INTO jira_connections(project_id, name, base_url, credential_id, jql)
VALUES (:'project', 'Internal Jira', 'https://jira.example.test', :'jira_credential', 'project = APP') RETURNING id AS jira \gset
SELECT pg_temp.assert_true((SELECT deployment = 'data_center' AND auth_kind = 'jira_pat'
    FROM jira_connections WHERE id = :'jira'), 'Jira Data Center and PAT are the defaults');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO jira_connections(project_id,name,base_url,credential_id,jql) VALUES (%L,%L,%L,%L,%L)',
    :'other_project', 'Wrong credential', 'https://jira.example.test', :'jira_credential', 'project = APP'
), '23503', 'credentials cannot cross projects');
INSERT INTO servers(project_id, name, hostname, username)
VALUES (:'project', 'Application server', '10.1.2.3', 'support') RETURNING id AS server \gset
SELECT pg_temp.assert_true((SELECT NOT allow_sudo AND port = 22 FROM servers WHERE id = :'server'),
    'server access defaults to SSH with sudo disabled');
SELECT pg_temp.assert_true((SELECT NOT ssh_allowed FROM servers WHERE id = :'server'), 'SSH permission defaults off');
UPDATE servers SET ssh_allowed = true WHERE id = :'server';
SELECT pg_temp.assert_true((SELECT ssh_allowed FROM server_assets WHERE id = :'server'), 'SSH permission is exposed in server assets');
UPDATE servers SET allow_sudo = true WHERE id = :'server';
SELECT pg_temp.assert_true((SELECT allow_sudo FROM servers WHERE id = :'server'), 'sudo policy can be enabled');
SELECT pg_temp.expect_failure(format('UPDATE servers SET port = 70000 WHERE id = %L', :'server'),
    '23514', 'invalid server ports rejected');
SELECT pg_temp.expect_failure(format('UPDATE servers SET credential_id = %L WHERE id = %L', :'jira_credential', :'server'),
    '23503', 'Jira token cannot be used as an SSH private key');

INSERT INTO jobs(project_id, kind, repository_id, idempotency_key)
VALUES (:'project', 'repository_sync', :'repo', 'sync-1') RETURNING id AS sync_job \gset
SELECT pg_temp.expect_failure(format(
    'INSERT INTO jobs(project_id,kind,repository_id) VALUES (%L,%L,%L)', :'project', 'repository_sync', :'repo'
), '23505', 'duplicate live repository sync rejected');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO jobs(project_id,kind,repository_id) VALUES (%L,%L,%L)', :'project', 'repository_sync', :'other_repo'
), '23503', 'job resource must belong to its project');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO jobs(project_id,kind,repository_id,document_set_id) VALUES (%L,%L,%L,%L)',
    :'project', 'repository_sync', :'repo', :'docs'
), '23514', 'job requires exactly one typed target');
UPDATE jobs SET status = 'failed', error = 'test failure', finished_at = now() WHERE id = :'sync_job';
SELECT pg_temp.expect_failure(format('UPDATE jobs SET status = %L, finished_at = NULL WHERE id = %L', 'queued', :'sync_job'),
    '23514', 'terminal job cannot be revived');
SELECT pg_temp.expect_failure(format('DELETE FROM jobs WHERE id = %L', :'sync_job'),
    '23514', 'job history cannot be deleted');
INSERT INTO jobs(project_id, kind, repository_id) VALUES (:'project', 'repository_sync', :'repo');
SELECT pg_temp.assert_true((SELECT count(*) = 2 FROM jobs WHERE repository_id = :'repo'),
    'retry creates a new job without losing failed history');

INSERT INTO jobs(project_id, kind, document_set_id) VALUES (:'project', 'documentation_generate', :'docs') RETURNING id AS docs_job \gset
INSERT INTO documentation_generations(job_id, project_id, document_set_id, mode, plugin_version, plugin_digest, base_revision)
VALUES (:'docs_job', :'project', :'docs', 'build', '0.3.2', repeat('a', 64), 0);
INSERT INTO documentation_generation_sources VALUES (:'docs_job', :'project', :'repo', repeat('a', 40));
INSERT INTO job_questions(job_id, question) VALUES (:'docs_job', 'What is the intended audience?') RETURNING id AS question \gset
UPDATE jobs SET status = 'waiting_input', started_at = now() WHERE id = :'docs_job';
UPDATE job_questions SET answer = 'Support engineers', answered_by = :'actor', answered_at = now() WHERE id = :'question';
SELECT pg_temp.assert_true((SELECT answer = 'Support engineers' FROM job_questions WHERE id = :'question'),
    'plugin questions and attributed answers persist');

INSERT INTO document_files(project_id, document_set_id, path)
VALUES (:'project', :'docs', 'how-to/start.md') RETURNING id AS file \gset
INSERT INTO document_files(project_id, document_set_id, path)
VALUES (:'project', :'docs', 'reference/obsolete.md') RETURNING id AS removed_file \gset
SELECT pg_temp.expect_failure(format(
    'INSERT INTO document_files(project_id,document_set_id,path) VALUES (%L,%L,%L)', :'project', :'docs', '../escape.md'
), '23514', 'path traversal rejected');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO document_files(project_id,document_set_id,path) VALUES (%L,%L,%L)', :'project', :'docs', '/etc/passwd'
), '23514', 'absolute file paths rejected');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO document_files(project_id,document_set_id,path) VALUES (%L,%L,%L)', :'project', :'docs', 'a\..\escape.md'
), '23514', 'Windows-style traversal rejected');

INSERT INTO document_revisions(file_id, project_id, document_set_id, content_hash, byte_size, origin, generation_job_id)
VALUES (:'file', :'project', :'docs', repeat('b', 64), 200, 'generated', :'docs_job') RETURNING id AS old_revision \gset
INSERT INTO document_revisions(file_id, project_id, document_set_id, content_hash, byte_size, origin)
VALUES (:'removed_file', :'project', :'docs', repeat('c', 64), 200, 'uploaded') RETURNING id AS removed_revision \gset
UPDATE document_files SET current_revision_id = :'old_revision' WHERE id = :'file';
UPDATE document_files SET current_revision_id = :'removed_revision' WHERE id = :'removed_file';
UPDATE document_sets SET revision = revision + 1 WHERE id = :'docs';
UPDATE documentation_generations SET published_revision = 1 WHERE job_id = :'docs_job';
UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE id = :'docs_job';
SELECT pg_temp.expect_failure(format('UPDATE documentation_generation_sources SET commit_sha = %L WHERE job_id = %L',
    repeat('f', 40), :'docs_job'), '23514', 'completed plugin source provenance is immutable');
SELECT pg_temp.expect_failure(format('UPDATE document_files SET current_revision_id = %L WHERE id = %L', :'removed_revision', :'file'),
    '23503', 'current revision must belong to the same file');
SELECT pg_temp.expect_failure(format('UPDATE document_revisions SET byte_size = 42 WHERE id = %L', :'old_revision'),
    '23514', 'document revisions are immutable');
SELECT pg_temp.expect_failure(format(
    'INSERT INTO document_revisions(file_id,project_id,document_set_id,content_hash,byte_size,origin) VALUES (%L,%L,%L,%L,10,%L)',
    :'file', :'project', :'other_docs', repeat('a', 64), 'uploaded'
), '23503', 'revision cannot cross documentation sets');

INSERT INTO jira_tickets(project_id, connection_id, remote_id, issue_key, jira_project_key, summary, raw_payload, source_updated_at)
VALUES (:'project', :'jira', '10001', 'APP-1', 'APP', 'First title', '{}', now());
INSERT INTO jira_tickets(project_id, connection_id, remote_id, issue_key, jira_project_key, summary, raw_payload, source_updated_at)
VALUES (:'project', :'jira', '10001', 'APP-2', 'APP', 'Updated title', '{}', now())
ON CONFLICT (connection_id, remote_id) DO UPDATE SET issue_key = EXCLUDED.issue_key, summary = EXCLUDED.summary;
SELECT pg_temp.assert_true((SELECT count(*) = 1 AND bool_and(summary = 'Updated title' AND issue_key = 'APP-2')
    FROM jira_tickets WHERE connection_id = :'jira'), 'Jira reimport updates the stable remote ID without duplicates');

INSERT INTO chat_sessions(project_id, owner_id) VALUES (:'project', :'actor') RETURNING id AS session \gset
INSERT INTO chat_items(session_id, position, payload) VALUES (:'session', 0, '{"role":"user","content":"Help"}');
INSERT INTO chat_turns(project_id, session_ref, actor_id, question, answer, not_covered)
VALUES (:'project', :'session', :'actor', 'Help', 'No documentation yet', true) RETURNING id AS turn \gset
INSERT INTO documentation_gaps(project_id, chat_turn_id, question) VALUES (:'project', :'turn', 'Help');
DELETE FROM chat_items WHERE session_id = :'session';
DELETE FROM chat_sessions WHERE id = :'session';
SELECT pg_temp.assert_true((SELECT count(*) = 1 FROM chat_turns WHERE id = :'turn'),
    'audit transcript survives chat deletion');
SELECT pg_temp.expect_failure(format('DELETE FROM chat_turns WHERE id = %L', :'turn'),
    '23514', 'audit transcript is append-only');
INSERT INTO audit_events(project_id, actor_id, action, resource_type, resource_id)
VALUES (:'project', :'actor', 'server.updated', 'server', :'server') RETURNING id AS audit \gset
SELECT pg_temp.expect_failure(format('UPDATE audit_events SET details = %L WHERE id = %L', '{"changed":true}', :'audit'),
    '23514', 'admin audit events are append-only');
SELECT pg_temp.assert_true((SELECT count(*) = 1 AND bool_and(version >= 24 AND NOT dirty)
    FROM public.golang_schema_migrations), 'golang-migrate records a clean current version after reapply');
ROLLBACK;
