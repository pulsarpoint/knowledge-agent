BEGIN;
SET search_path = ka, public;

-- Server discovery: an ad-hoc agent session (purpose discovery) explores one server over SSH and proposes services and
-- documents; an administrator accepts or rejects each proposal. Several runs may explore one server at once.
ALTER TABLE agent_sessions
    ADD COLUMN server_id uuid REFERENCES servers(id) ON DELETE CASCADE,
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery')),
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery')),
    ADD CONSTRAINT agent_sessions_discovery_server CHECK ((purpose = 'discovery') = (server_id IS NOT NULL));
CREATE INDEX agent_sessions_discovery ON agent_sessions(server_id, created_at DESC) WHERE server_id IS NOT NULL;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id).';
COMMENT ON COLUMN agent_sessions.server_id IS 'The server a discovery run explores; set exactly when purpose is discovery.';

CREATE TABLE discovery_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    session_id uuid NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    server_id uuid NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    number integer NOT NULL CHECK (number >= 1),
    kind text NOT NULL CHECK (kind IN ('service', 'document')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    service_id uuid REFERENCES services(id) ON DELETE SET NULL,
    service_proposal_id uuid REFERENCES discovery_proposals(id) ON DELETE CASCADE,
    document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    decided_by uuid REFERENCES principals(id),
    decided_at timestamptz,
    result_id uuid,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, number),
    CONSTRAINT discovery_proposals_document_fields CHECK (kind = 'document' OR (service_proposal_id IS NULL AND document_id IS NULL)),
    CONSTRAINT discovery_proposals_one_service CHECK (service_id IS NULL OR service_proposal_id IS NULL),
    CONSTRAINT discovery_proposals_decided CHECK ((status = 'pending') = (decided_at IS NULL)),
    CONSTRAINT discovery_proposals_result CHECK (status <> 'accepted' OR result_id IS NOT NULL)
);
CREATE INDEX discovery_proposals_server ON discovery_proposals(server_id, status);
CREATE INDEX discovery_proposals_service_proposal ON discovery_proposals(service_proposal_id) WHERE service_proposal_id IS NOT NULL;
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON discovery_proposals
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE discovery_proposals IS
    'What a discovery run proposes for the inventory: a service (service_id: the existing service it updates) or a document (service_id or service_proposal_id: the service it is about, else the server; document_id: the document it becomes a new version of). Accepted or rejected by an administrator.';

CREATE FUNCTION seed_server_discovery_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:server-discovery:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:server-discovery:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Server discovery','Explore one server over SSH and propose its services and documents for review.',
           'server',ARRAY['server-discovery','ssh'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Explore this server and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, for example: systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE FUNCTION seed_server_discovery_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_server_discovery_prompt(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_server_discovery AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_server_discovery_on_project();
SELECT seed_server_discovery_prompt(id) FROM projects;

-- Discovery documents are generated without a (retired, 0073) one-shot generation run: generated no longer needs one;
-- a version that has a generation_id is still generated.
ALTER TABLE document_versions
    DROP CONSTRAINT document_versions_check1,
    ADD CONSTRAINT document_versions_generation_origin CHECK (generation_id IS NULL OR origin = 'generated');

COMMIT;
