BEGIN;
SET search_path=ka,public;
CREATE TABLE documentation_workflows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL CHECK(length(name) BETWEEN 1 AND 240),
    scope text NOT NULL CHECK(scope IN ('service','environment')),
    service_id uuid,
    environment_id uuid,
    drafts jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(drafts)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id,project_id),
    FOREIGN KEY(service_id,project_id) REFERENCES services(id,project_id) ON DELETE SET NULL (service_id),
    FOREIGN KEY(environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE SET NULL (environment_id),
    -- Keep history when its inventory subject is deleted. Creation requires a live subject.
    CHECK((scope='service' AND environment_id IS NULL)
       OR (scope='environment' AND service_id IS NULL))
);
CREATE TABLE documentation_workflow_jobs (
    workflow_id uuid NOT NULL,
    job_id uuid NOT NULL,
    project_id uuid NOT NULL,
    PRIMARY KEY(workflow_id,job_id),
    FOREIGN KEY(workflow_id,project_id) REFERENCES documentation_workflows(id,project_id),
    FOREIGN KEY(job_id,project_id) REFERENCES jobs(id,project_id)
);
CREATE INDEX documentation_workflow_jobs_job ON documentation_workflow_jobs(job_id);
CREATE INDEX documentation_workflows_recent ON documentation_workflows(project_id,updated_at DESC);

-- Group historical service/environment attempts without mutating immutable generations.
CREATE TEMP TABLE workflow_backfill ON COMMIT DROP AS
SELECT g.*,md5('knowledge-agent-v3:documentation-workflow:' || project_id::text || ':' ||
    (source_snapshot->>'scope') || ':' || COALESCE(source_snapshot->>'service_id',source_snapshot->>'environment_id'))::uuid AS workflow_id
FROM documentation_generations g WHERE source_snapshot->>'workflow_version'='2';
INSERT INTO documentation_workflows(id,project_id,name,scope,service_id,environment_id,drafts,created_at,updated_at)
SELECT DISTINCT ON (workflow_id) workflow_id,project_id,
    left(COALESCE(source_snapshot->'service'->>'name',source_snapshot->'environment'->>'name','Documentation') || ' documentation',240),
    source_snapshot->>'scope',
    (SELECT s.id FROM services s WHERE s.id::text=source_snapshot->>'service_id' AND s.project_id=b.project_id),
    CASE WHEN source_snapshot->>'scope'='environment' THEN
        (SELECT e.id FROM environments e WHERE e.id::text=source_snapshot->>'environment_id' AND e.project_id=b.project_id) END,
    jsonb_build_object('form',jsonb_build_object('value',jsonb_build_object(
        'scope',source_snapshot->'scope','service_id',source_snapshot->'service_id',
        'environment_id',source_snapshot->'environment_id','model_id',source_snapshot->'model_id',
        'code_project_ids',source_snapshot->'code_project_ids','server_ids',source_snapshot->'server_ids',
        'units','[]'::jsonb,'instructions',instructions,'update_existing',true))),
    min(created_at) OVER (PARTITION BY workflow_id),created_at
FROM workflow_backfill b ORDER BY workflow_id,created_at DESC,job_id DESC;
INSERT INTO documentation_workflow_jobs(workflow_id,job_id,project_id)
SELECT workflow_id,job_id,project_id FROM workflow_backfill;
INSERT INTO documentation_workflow_jobs(workflow_id,job_id,project_id)
SELECT DISTINCT b.workflow_id,j.id,b.project_id FROM workflow_backfill b
CROSS JOIN LATERAL jsonb_each_text(COALESCE(b.source_snapshot->'code_graph_hashes','{}')) h
JOIN jobs j ON j.project_id=b.project_id AND j.repository_id::text=h.key AND j.kind='code_index'
    AND j.status='succeeded' AND j.result->>'source_hash'=h.value
ON CONFLICT DO NOTHING;
-- Recover the latest exact prompt and context of each standalone phase.
WITH latest AS (
    SELECT DISTINCT ON (workflow_id,p->>'kind') workflow_id,p->>'kind' AS phase,p,instructions
    FROM workflow_backfill CROSS JOIN LATERAL jsonb_array_elements(prompt_snapshot) p
    WHERE p->>'kind' IN ('overview','api') ORDER BY workflow_id,p->>'kind',created_at DESC,job_id DESC
), drafts AS (
    SELECT workflow_id,jsonb_object_agg(phase,jsonb_build_object('content',p->>'content','instructions',instructions)) AS values
    FROM latest GROUP BY workflow_id
)
UPDATE documentation_workflows w SET drafts=w.drafts || d.values FROM drafts d WHERE d.workflow_id=w.id;
WITH latest AS (
    SELECT DISTINCT ON (workflow_id,p->>'kind') workflow_id,p->>'kind' AS phase,p
    FROM workflow_backfill CROSS JOIN LATERAL jsonb_array_elements(prompt_snapshot) p
    WHERE p->>'kind' NOT IN ('overview','api')
    ORDER BY workflow_id,p->>'kind',created_at DESC,job_id DESC
), selections AS (
    SELECT workflow_id,jsonb_object_agg(phase,jsonb_build_object(
        'id',p->'id','versionId',p->'version_id','version',p->'version',
        'activeContent',COALESCE(p->'active_content',p->'content'),'content',p->'content')) AS value
    FROM latest GROUP BY workflow_id
)
UPDATE documentation_workflows w SET drafts=jsonb_set(w.drafts,'{form,selection}',s.value)
FROM selections s WHERE s.workflow_id=w.id;
CREATE VIEW documentation_workflow_details AS
SELECT w.*,
 COALESCE((SELECT jsonb_agg(p.data) FROM (
   SELECT DISTINCT ON (prompt->>'kind') jsonb_build_object('kind',prompt->>'kind','job_id',j.id,'status',j.status,'error',j.error,'created_at',j.created_at) AS data
   FROM documentation_workflow_jobs link JOIN jobs j ON j.id=link.job_id
   JOIN documentation_generations g ON g.job_id=j.id CROSS JOIN LATERAL jsonb_array_elements(g.prompt_snapshot) prompt
   WHERE link.workflow_id=w.id ORDER BY prompt->>'kind',j.created_at DESC,j.id DESC
 ) p),'[]'::jsonb) AS phases,
 COALESCE((SELECT jsonb_agg(p.data) FROM (
   SELECT DISTINCT ON (j.repository_id) jsonb_build_object('job_id',j.id,'repository_id',j.repository_id,'status',j.status,'error',j.error,'result',j.result) AS data
   FROM documentation_workflow_jobs link JOIN jobs j ON j.id=link.job_id
   WHERE link.workflow_id=w.id AND j.kind='code_index' ORDER BY j.repository_id,j.created_at DESC,j.id DESC
 ) p),'[]'::jsonb) AS graph_runs
FROM documentation_workflows w;
COMMIT;
