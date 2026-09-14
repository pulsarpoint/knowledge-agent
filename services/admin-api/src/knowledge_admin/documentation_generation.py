"""Queue scoped documentation work and run it with the existing Agents SDK runtime."""

import asyncio
import base64
import copy
import json
import os
import re
import signal
import socket
import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.agents import agent, load_credentials
from knowledge_llm import code_compatible
from knowledge_admin.documents import DocumentInput, export_document, save_document
from knowledge_admin.repositories import RepositoryStore


class GenerationUnit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['code', 'diagram', 'deployment', 'architecture', 'infrastructure']
    prompt_id: UUID
    prompt_version_id: UUID
    prompt_override: str | None = Field(default=None, min_length=1, max_length=100000)

    @model_validator(mode='after')
    def valid_override(self):
        if self.prompt_override is not None and (not self.prompt_override.strip() or '\x00' in self.prompt_override):
            raise ValueError('Enter a nonempty prompt without null characters')
        return self


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['environment', 'service']
    model_id: UUID
    service_id: UUID | None = None
    environment_id: UUID | None = None
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    units: list[GenerationUnit] = Field(min_length=1, max_length=3)
    instructions: str = Field(default='', max_length=20000)
    update_existing: bool = False

    @model_validator(mode='after')
    def validate_scope(self):
        kinds = [unit.kind for unit in self.units]
        if len(kinds) != len(set(kinds)):
            raise ValueError('Choose each document type once')
        if self.scope == 'environment':
            if not set(kinds) <= {'architecture', 'infrastructure'} or self.environment_id is None or self.service_id is not None or self.code_project_ids:
                raise ValueError('Select an environment and its overview or server infrastructure documentation')
        elif self.service_id is None or {'architecture', 'infrastructure'}.intersection(kinds):
            raise ValueError('Select a service and its documentation steps')
        if 'code' in kinds and not self.code_project_ids:
            raise ValueError('Select code projects for code analysis')
        if 'deployment' in kinds and (self.environment_id is None or not self.server_ids):
            raise ValueError('Select an environment and servers for deployment inspection')
        if 'infrastructure' in kinds and len(self.server_ids) != 1:
            raise ValueError('Select exactly one server for infrastructure architecture')
        if not {'deployment', 'infrastructure'}.intersection(kinds) and self.server_ids:
            raise ValueError('Server access is only used by deployment or infrastructure documentation')
        if not {'code', 'diagram'}.intersection(kinds) and self.code_project_ids:
            raise ValueError('Code projects are only used by code analysis and diagrams')
        if '\x00' in self.instructions:
            raise ValueError('Instructions cannot contain null characters')
        self.code_project_ids = list(dict.fromkeys(self.code_project_ids))
        self.server_ids = list(dict.fromkeys(self.server_ids))
        return self


class GeneratedDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(max_length=4000)
    markdown: str = Field(min_length=1, max_length=900000)


class Component(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,49}$')
    label: str = Field(min_length=1, max_length=180)
    parent_id: str | None


class Connection(BaseModel):
    source: str
    target: str
    label: str = Field(max_length=180)


class ComponentGraph(BaseModel):
    nodes: list[Component] = Field(min_length=1, max_length=60)
    edges: list[Connection] = Field(max_length=120)
    explanation: str = Field(max_length=12000)

    @model_validator(mode='after')
    def valid_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError('Component IDs must be unique')
        for node in self.nodes:
            seen = {node.id}
            parent = node.parent_id
            while parent is not None:
                if parent not in nodes or parent in seen or len(seen) > 4:
                    raise ValueError('Invalid component nesting')
                seen.add(parent)
                parent = nodes[parent].parent_id
        if any(edge.source not in nodes or edge.target not in nodes for edge in self.edges):
            raise ValueError('Connections must reference existing components')
        return self


def graph_source(graph: ComponentGraph) -> str:
    """Serialize plain labels and validated relationships; model output is never executable D2."""
    nodes = {node.id: node for node in graph.nodes}
    ids = {node.id: f'n{index}' for index, node in enumerate(graph.nodes)}
    def path(node_id):
        node = nodes[node_id]
        return (path(node.parent_id) + '.' if node.parent_id is not None else '') + ids[node_id]
    def label(value):
        return json.dumps(value, ensure_ascii=False).replace('$', r'\$')
    lines = ['direction: right']
    for node in sorted(graph.nodes, key=lambda node: path(node.id).count('.')):
        lines.append(f'{path(node.id)}: {label(node.label)} {{style.fill: "#dbeafe"; style.stroke: "#2563eb"}}')
    for edge in graph.edges:
        lines.append(f'{path(edge.source)} -> {path(edge.target)}: {label(edge.label)}')
    return '\n'.join(lines) + '\n'


def environment_views(sources: dict) -> str:
    """Show functional, deployment and endpoint views without asserting network reachability."""
    services = sources['services']
    servers = sources['environment']['servers']
    if not services and not servers:
        return ''
    service_ids = {s['id']: f's{i}' for i, s in enumerate(services)}
    server_ids = {s['id']: f'h{i}' for i, s in enumerate(servers)}
    hosts = {s['id']: s for s in servers}
    functional, deployment, network = (['direction: right'] for _ in range(3))

    def label(value: str) -> str:
        return json.dumps(value, ensure_ascii=False).replace('$', r'\$')

    for server in servers:
        deployment.append(f'{server_ids[server["id"]]}: {label(server["name"] + chr(10) + server["hostname"])} {{style.fill: "#dcfce7"}}')
    for service in services:
        sid = service_ids[service['id']]
        functional.append(f'{sid}: {label(service["name"])} {{style.fill: "#dbeafe"}}')
        deployment.append(f'{sid}: {label(service["name"])} {{style.fill: "#dbeafe"}}')
        endpoints = []
        for record in service['deployments']:
            host = hosts.get(record['server_id'])
            if host is None:
                continue
            endpoint = host['hostname'] + (f":{record['port']}" if record['port'] is not None else ':port unknown')
            endpoints.append(endpoint)
            runtime = ' / '.join(v for v in (record['runtime'], record['runtime_name']) if v)
            deployment.append(f'{sid} -> {server_ids[host["id"]]}: {label("recorded deployment" + (": " + runtime if runtime else ""))}')
        endpoint_text = '\n'.join(dict.fromkeys(endpoints)) or 'No endpoint recorded'
        network.append(f'{sid}: {label(service["name"] + chr(10) + endpoint_text)} {{style.fill: "#fef3c7"}}')
        for dependency in service['dependencies']:
            target = service_ids.get(dependency['service_id'])
            if target is None:
                continue
            purpose = dependency['description'].strip() or 'depends on; purpose not recorded'
            functional.append(f'{sid} -> {target}: {label(purpose[:180] + ("…" if len(purpose) > 180 else ""))}')
            network.append(f'{sid} -> {target}: "dependency; network route unverified" {{style.stroke-dash: 4}}')
    views = [
        ('Functional service relationships', 'Arrows mean “depends on”, not necessarily request or data-flow direction. Labels come from recorded relationship descriptions.', functional),
        ('Deployment and runtime mapping', 'Membership alone does not imply deployment. Lines show explicit deployment records in this environment; isolated services have no recorded placement.', deployment),
        ('Networking reference', 'Node labels show recorded deployment hostnames and service ports, not verified listeners. Dashed arrows are logical dependencies: they do not prove direct connections, traffic direction, transport, TLS or firewall access. Missing ports remain unknown.', network),
    ]
    return '\n\n'.join(f'## {title}\n\n{description}\n\n```d2\n' + '\n'.join(lines) + '\n```'
                          for title, description, lines in views if len(lines) > 1) + '\n'


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    @router.post('/generations', status_code=202)
    def generate(value: GenerationInput, request: Request) -> dict:
        if store is None:
            raise HTTPException(503, 'Document storage is not configured')
        subject, email = request_actor(request)
        with store.connect() as conn:
            project = store.project(conn, app_name)
            actor = store.actor(conn, subject, email)
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR UPDATE', (project,))
            model = conn.execute("""SELECT id,name,provider,model,api_format,base_url,aws_region,enabled,revision,
                api_key_ciphertext IS NOT NULL AS has_key FROM ka.llm_models WHERE id=%s""", (value.model_id,)).fetchone()
            if model is None or not model['enabled'] or not code_compatible(model['provider'],model['api_format'],model['model']):
                raise HTTPException(400, 'Select an enabled model that supports documentation generation')
            model['api_key_ref'] = {'table': 'ka.llm_models', 'id': model['id'], 'aad': f"llm:{model['id']}:v1"} if model.pop('has_key') else None
            service = None
            if value.service_id is not None:
                service = conn.execute('SELECT * FROM ka.service_assets WHERE id=%s AND project_id=%s', (value.service_id, project)).fetchone()
                if service is None:
                    raise HTTPException(404, 'Service not found')
            environment = None
            services = [service] if service is not None else []
            if value.environment_id is not None:
                environment = conn.execute('SELECT * FROM ka.environment_assets WHERE id=%s AND project_id=%s', (value.environment_id, project)).fetchone()
                if environment is None:
                    raise HTTPException(404, 'Environment not found')
                members = {s['id'] for s in environment['services']}
                if service is not None and str(value.service_id) not in members:
                    raise HTTPException(400, 'The service must belong to the selected environment')
                if not {str(s) for s in value.server_ids} <= {s['id'] for s in environment['servers']}:
                    raise HTTPException(400, 'Select servers that belong to this environment')
                if value.scope == 'environment':
                    services = conn.execute('SELECT * FROM ka.service_assets WHERE project_id=%s AND id=ANY(%s) ORDER BY name,id',
                                            (project,[UUID(s) for s in members])).fetchall()
                else:
                    services = copy.deepcopy(services)
                for item in services:
                    item['dependencies'] = [s for s in item['dependencies'] if s['service_id'] in members]
                    item['dependents'] = [s for s in item['dependents'] if s['service_id'] in members]
                    item['deployments'] = [d for d in item['deployments'] if d['server_id'] in {s['id'] for s in environment['servers']}
                        and (d.get('environment_id') == str(environment['id']) if d.get('environment_id') is not None else d['environment'] == environment['name'])]
            kinds = {u.kind for u in value.units}
            if service is not None:
                if 'code' in kinds and service['service_type'] != 'custom':
                    raise HTTPException(400, 'Code analysis requires a Custom service')
                if 'diagram' in kinds and service['service_type'] == 'custom' and not value.code_project_ids:
                    raise HTTPException(400, 'Select code projects for the custom service diagram')
                if not {str(p) for p in value.code_project_ids} <= {p['id'] for p in service['repositories'] if not p['archived']}:
                    raise HTTPException(400, 'Select code projects linked to this service')
            code_projects = conn.execute("""SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,r.current_commit,
                ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
                FROM ka.repositories r WHERE project_id=%s AND id=ANY(%s) AND archived_at IS NULL ORDER BY id""", (project,value.code_project_ids)).fetchall()
            if len(code_projects) != len(value.code_project_ids) or any(not p['folder_paths'] for p in code_projects):
                raise HTTPException(400, 'Each code project needs an available mapped folder')
            code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
            if code_projects and (not code_root.is_absolute() or '..' in code_root.parts):
                raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
            for code_project in code_projects:
                code_project['folder_paths'] = [str(code_root / path) for path in code_project['folder_paths']]
            servers = conn.execute("""SELECT s.id,s.name,s.hostname,s.description,s.tags,s.port,s.username,s.allow_sudo,
                s.known_hosts,s.ssh_allowed,s.enabled,c.id AS credential_id,c.key_version
                FROM ka.servers s LEFT JOIN ka.credentials c ON c.id=s.credential_id AND c.kind='ssh_private_key'
                WHERE s.project_id=%s AND s.id=ANY(%s) ORDER BY s.id""", (project,value.server_ids)).fetchall()
            if len(servers) != len(value.server_ids) or any(not s['ssh_allowed'] or not s['enabled'] or not s['username'] or s['credential_id'] is None for s in servers):
                raise HTTPException(400, 'Each selected server needs SSH enabled, a login user and a saved global private key')
            for server in servers:
                credential_id, key_version = server.pop('credential_id'), server.pop('key_version')
                server['private_key_ref'] = {'table': 'ka.credentials', 'id': credential_id,
                    'aad': f'{project}:{credential_id}:ssh_private_key:{key_version}'}
                server['instructions'] = ''
                server['skills'], server['mcp_servers'] = [], []
            sources = jsonable_encoder({**value.model_dump(), 'workflow_version': 2, 'service': service,
                'services': services, 'environment': environment, 'code_projects': code_projects})
            if value.scope == 'environment' and 'architecture' in kinds:
                # Pin the current evidence versions; later document edits cannot alter this run.
                sources['supporting_documents'] = jsonable_encoder(conn.execute("""SELECT d.id,d.current_version_id AS version_id,
                    d.version,d.title,d.purpose,d.content_hash,d.environment_ids,d.service_ids,d.server_ids,d.code_project_ids,v.created_at AS documented_at,v.markdown
                    FROM ka.document_details d JOIN ka.document_versions v ON v.id=d.current_version_id
                    WHERE d.project_id=%s AND d.purpose<>'architecture' AND
                    (d.environment_ids=ARRAY[%s]::uuid[] OR
                     (cardinality(d.environment_ids)=0 AND d.purpose IN ('code','diagram') AND
                      (d.service_ids && %s::uuid[] OR d.code_project_ids && %s::uuid[])))
                    ORDER BY d.purpose,d.title,d.id""", (project,value.environment_id,[s['id'] for s in services],
                        [p['id'] for s in services for p in s['repositories'] if not p['archived']])).fetchall())
            prompts, configs, targets = [], {}, []
            for unit in sorted(value.units, key=lambda u: ['code','diagram','deployment','infrastructure','architecture'].index(u.kind)):
                row = conn.execute("""SELECT p.id,p.name,p.input_kind,v.id AS version_id,v.version,v.content
                    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                    WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (unit.prompt_id, project)).fetchone()
                expected_kind = {'code':'code','diagram':'code','deployment':'server','architecture':'mixed','infrastructure':'server'}[unit.kind]
                if row is None or row['input_kind'] not in (expected_kind,'mixed'):
                    raise HTTPException(400, 'Select an active prompt matching this documentation step')
                if row['version_id'] != unit.prompt_version_id:
                    raise HTTPException(409, 'An active prompt changed. Reload it before generating documentation.')
                row['active_content'] = row['content']
                row['content'] = unit.prompt_override if unit.prompt_override is not None else row['content']
                row['overridden'] = row['content'] != row['active_content']
                prompts.append({**row, 'kind': unit.kind})
                # Fixed workflow specialists, never registered as configurable AI agent profiles.
                reads_code = unit.kind in ('code','diagram') and bool(code_projects)
                uses_ssh = unit.kind in ('deployment','infrastructure')
                configs[unit.kind] = jsonable_encoder({
                    'id': f'documentation-{unit.kind}', 'name': f'Documentation: {unit.kind}',
                    'description': 'Built-in documentation step', 'enabled': True, 'model': model,
                    'system_prompt': {'content': row['content']}, 'instructions': '',
                    'code': {'enabled': reads_code, 'projects': code_projects if reads_code else []},
                    'ssh': {'enabled': uses_ssh, 'servers': servers if uses_ssh else []},
                    'documents': {'enabled': False, 'folders': []}, 'mcp': {'enabled': False, 'servers': []},
                    'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []},
                })
                subject_asset = environment if value.scope == 'environment' else service
                slug = re.sub(r'[^a-z0-9]+','-',subject_asset['name'].lower()).strip('-')[:40] or value.scope
                slug += '-' + str(subject_asset['id'])[:8] + '-' + unit.kind
                if unit.kind == 'deployment':
                    slug += '-' + str(value.environment_id)[:8]
                elif unit.kind == 'infrastructure':
                    slug += '-' + str(value.server_ids[0])[:8]
                existing = conn.execute("""SELECT d.id,d.revision,v.markdown FROM ka.documents d
                    JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.project_id=%s AND d.slug=%s""", (project,slug)).fetchone()
                if existing is not None and not value.update_existing:
                    raise HTTPException(409, 'These documents already exist. Enable "Create new versions of existing documents" to update them.')
                targets.append({'kind': unit.kind, 'slug': slug, 'existing': existing})
            if conn.execute("""SELECT 1 FROM ka.documentation_generations g JOIN ka.jobs j ON j.id=g.job_id
                WHERE g.project_id=%s AND j.status IN ('queued','running') AND
                ((%s::uuid IS NOT NULL AND g.source_snapshot->>'service_id'=%s) OR
                 (%s::uuid IS NULL AND g.source_snapshot->>'scope'='environment' AND g.source_snapshot->>'environment_id'=%s))""",
                (project,value.service_id,str(value.service_id),value.service_id,str(value.environment_id))).fetchone():
                raise HTTPException(409, 'Documentation for this selection is already queued or running')
            sources['targets'] = targets
            job_id = uuid4()
            conn.execute("""INSERT INTO ka.jobs(id,project_id,kind,requested_by,idempotency_key)
                VALUES (%s,%s,'documentation_generate',%s,%s)""", (job_id,project,actor,str(job_id)))
            conn.execute("""INSERT INTO ka.documentation_generations(job_id,project_id,mode,instructions,agent_snapshot,prompt_snapshot,source_snapshot)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (job_id,project,'update' if value.update_existing else 'create',value.instructions,
                Jsonb(configs),Jsonb(jsonable_encoder(prompts)),Jsonb(jsonable_encoder(sources))))
            conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id)
                VALUES (%s,%s,'documentation.generation_requested','job',%s)""", (project,actor,job_id))
            return {'job_id': job_id}

    return router


async def generate_documents(store: RepositoryStore, job: dict) -> list[tuple[dict, GeneratedDocument]]:
    sources = job['source_snapshot']
    if sources.get('workflow_version') != 2:
        raise HTTPException(409, 'This request uses the old generation workflow. Submit it again with the new documentation form.')
    configs = job['agent_snapshot']
    results = []
    code_analysis = ''
    infrastructure_findings = None
    for target in sources['targets']:
        kind = target['kind']
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress',%s)", (job['id'], f'Generating {kind} documentation'))
        config = copy.deepcopy(configs[kind])
        unit_sources = {k: v for k, v in sources.items() if k not in ('targets','units','service','supporting_documents')}
        if kind in ('code','diagram'):
            service = copy.deepcopy(sources['service'])
            service['deployments'] = []
            unit_sources.update(environment=None, environment_id=None, server_ids=[], services=[service])
        elif kind in ('deployment','infrastructure'):
            selected = set(sources['server_ids'])
            unit_sources = copy.deepcopy(unit_sources)
            for item in unit_sources['services']:
                item['deployments'] = [d for d in item['deployments'] if d['server_id'] in selected]
            if kind == 'infrastructure':
                # Only this host and its recorded environment deployments are inspection inputs.
                unit_sources['services'] = [s for s in unit_sources['services'] if s['deployments']]
                unit_sources['environment']['servers'] = [s for s in unit_sources['environment']['servers'] if s['id'] in selected]
                unit_sources['environment']['services'] = [{'id': s['id'], 'name': s['name']} for s in unit_sources['services']]
        elif kind == 'architecture':
            unit_sources = {**unit_sources, 'server_ids': [], 'supporting_documents': sources.get('supporting_documents', []),
                            'infrastructure_findings': infrastructure_findings}
        if kind == 'diagram' and code_analysis:
            config['code'] = {'enabled': False, 'projects': []}
        task = json.dumps({'sources': unit_sources, 'existing_document': target['existing'],
                           'code_analysis': code_analysis, 'observation_time': datetime.now(timezone.utc).isoformat(),
                           'document_kind': kind}, ensure_ascii=False)
        credentials = load_credentials(store, config)
        # Background documents use complete structured responses; Bedrock streaming
        # can drop long JSON fields. Job progress and tool hooks remain available.
        result = await agent(config, task, credentials=credentials, timeout_seconds=600, max_turns=30, max_output_tokens=16384, stream=False,
                             output_type=ComponentGraph if kind == 'diagram' else GeneratedDocument)
        if kind == 'diagram':
            graph = ComponentGraph.model_validate(result.final_output)
            document = GeneratedDocument(title=sources['service']['name'] + ' component architecture',
                description='Service components and their relationships.',
                markdown='# Component architecture\n\n' + graph.explanation + '\n\n```d2\n' + graph_source(graph) + '```\n')
        else:
            document = GeneratedDocument.model_validate(result.final_output)
            if kind == 'code':
                code_analysis = document.markdown
            elif kind == 'infrastructure':
                infrastructure_findings = {'server_ids': sources['server_ids'], 'document': document.model_dump()}
            elif kind == 'architecture':
                document.markdown += '\n\n' + environment_views(sources)
        DocumentInput(slug=target['slug'],title=document.title,description=document.description,markdown=document.markdown)
        results.append((target,document))
    return results


def run_once(store: RepositoryStore) -> bool:
    lease = uuid4()
    with store.connect() as conn:
        conn.execute("""UPDATE ka.jobs SET status='failed',error='Documentation worker stopped. Generate again to retry.',
            finished_at=now(),worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
            WHERE kind='documentation_generate' AND status='running' AND lease_expires_at<now()""")
        job = conn.execute("""SELECT j.*,g.agent_snapshot,g.prompt_snapshot,g.source_snapshot FROM ka.jobs j
            JOIN ka.documentation_generations g ON g.job_id=j.id WHERE j.status='queued'
            AND j.kind='documentation_generate' ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1""").fetchone()
        if job is None:
            return False
        conn.execute("""UPDATE ka.jobs SET status='running',started_at=now(),attempt=attempt+1,worker_id=%s,
            lease_token=%s,lease_expires_at=now()+interval '40 minutes' WHERE id=%s""", (socket.gethostname(), lease, job['id']))
    try:
        results = asyncio.run(generate_documents(store, job))
        sources = job['source_snapshot']
        exports = []
        with store.connect() as conn:
            active = conn.execute("SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() FOR UPDATE", (job['id'], lease)).fetchone()
            if active is None:
                return True
            saved = []
            for target, document in results:
                shared = target['kind'] in ('code','diagram')
                architecture = target['kind'] == 'architecture'
                infrastructure = target['kind'] == 'infrastructure'
                linked_services = sources['services']
                if infrastructure:
                    linked_services = [s for s in linked_services if any(d['server_id'] in sources['server_ids'] for d in s['deployments'])]
                existing = target['existing']
                value = DocumentInput(slug=target['slug'], title=document.title, description=document.description,
                    markdown=document.markdown, purpose=target['kind'],
                    service_ids=[s['id'] for s in linked_services] if architecture or infrastructure else [sources['service_id']],
                    code_project_ids=list({p['id'] for s in linked_services for p in s['repositories'] if not p['archived']}) if architecture or infrastructure else sources['code_project_ids'],
                    deployment_ids=[d['id'] for s in linked_services for d in s['deployments'] if d['server_id'] in sources['server_ids']] if target['kind'] in ('deployment','infrastructure') else [],
                    environment_ids=[] if shared else [sources['environment_id']],
                    server_ids=[s['id'] for s in sources['environment']['servers']] if architecture else sources['server_ids'] if target['kind'] in ('deployment','infrastructure') else [],
                    revision=existing['revision'] if existing is not None else None)
                saved.append(save_document(conn, job['project_id'], job['requested_by'], value,
                    UUID(existing['id']) if existing is not None else None, job['id']))
                exports.append({'slug': value.slug, 'revision': saved[-1]['revision'], 'markdown': value.markdown})
            folders = []
            export_error = None
            try:
                for document in exports:
                    folders.append(export_document(document))
            except (OSError, ValueError):
                export_error = 'Documents were saved. File export failed; retry Export to folder from the document page.'
            conn.execute("""UPDATE ka.jobs SET status='succeeded',result=%s,finished_at=now(),worker_id=NULL,
                lease_token=NULL,lease_expires_at=NULL WHERE id=%s""",
                (Jsonb(jsonable_encoder({'documents': saved, 'output_folders': folders, 'export_error': export_error})), job['id']))
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'succeeded','Documents saved for review')", (job['id'],))
    except Exception as exc:
        # Provider/tool errors can contain request bodies or secrets. Never copy those to jobs or logs.
        error = 'Documentation generation failed. Check the model, selected resources and documentation prompts, then retry.'
        if isinstance(exc, HTTPException):
            error = str(exc.detail)
        with store.connect() as conn:
            conn.execute("""UPDATE ka.jobs SET status='failed',error=%s,finished_at=now(),worker_id=NULL,
                lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND lease_token=%s AND status='running'""", (error, job['id'], lease))
    return True


def main() -> None:
    from knowledge_admin.agent_tracing import setup
    setup()
    store = RepositoryStore('', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL'])
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            worked = run_once(store)
        except psycopg.Error:
            print('Documentation worker database unavailable; retrying', flush=True)
            worked = False
        if not worked:
            stopped.wait(2)
