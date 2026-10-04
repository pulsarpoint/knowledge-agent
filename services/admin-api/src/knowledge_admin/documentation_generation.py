"""Queue scoped documentation work and run it with the existing Agents SDK runtime."""

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import signal
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from agents import ItemHelpers, MaxTurnsExceeded, ModelBehaviorError
from agents.usage import Usage
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.agents import agent, load_credentials
from knowledge_llm import code_compatible
from knowledge_admin.documents import DocumentInput, GitCommit, export_document, save_document
from knowledge_admin.documentation_updates import collect_source_commits
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.code_processing import load_code_graph, CodeProcessingError
from knowledge_admin.api_evidence import api_batches, API_EVIDENCE_VERSION


class GenerationUnit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['overview', 'api', 'messaging', 'code', 'diagram', 'deployment', 'deployment_diagram', 'architecture', 'infrastructure']
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
    workflow_id: UUID | None = None
    scope: Literal['environment', 'service']
    model_id: UUID
    service_id: UUID | None = None
    environment_id: UUID | None = None
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    units: list[GenerationUnit] = Field(min_length=1, max_length=4)
    code_graph_hashes: dict[str, str] = Field(default_factory=dict, max_length=100)
    source_commits: dict[UUID, GitCommit] = Field(default_factory=dict, max_length=100)
    instructions: str = Field(default='', max_length=20000)
    update_existing: bool = False

    @model_validator(mode='after')
    def validate_scope(self):
        kinds = [unit.kind for unit in self.units]
        if len(kinds) != len(set(kinds)):
            raise ValueError('Choose each document type once')
        if {'overview', 'api', 'messaging'}.intersection(kinds) and (len(kinds) != 1 or self.environment_id is not None):
            raise ValueError('Overview, API and messaging documentation run as separate phases, without an environment')
        if self.scope == 'environment':
            if not set(kinds) <= {'architecture', 'infrastructure'} or self.environment_id is None or self.service_id is not None or self.code_project_ids:
                raise ValueError('Select an environment and its overview or server infrastructure documentation')
        elif self.service_id is None or {'architecture', 'infrastructure'}.intersection(kinds):
            raise ValueError('Select a service and its documentation steps')
        if {'deployment', 'deployment_diagram'}.intersection(kinds) and (self.environment_id is None or not self.server_ids):
            raise ValueError('Select an environment and servers for deployment inspection')
        if 'infrastructure' in kinds and len(self.server_ids) != 1:
            raise ValueError('Select exactly one server for infrastructure architecture')
        if not {'deployment', 'deployment_diagram', 'infrastructure'}.intersection(kinds) and self.server_ids:
            raise ValueError('Server access is only used by deployment or infrastructure documentation')
        if not {'overview', 'api', 'messaging', 'code', 'diagram'}.intersection(kinds) and self.code_project_ids:
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


class CodeDocumentation(GeneratedDocument):
    markdown: str = Field(min_length=200, max_length=900000)
    diagram: ComponentGraph | None = None

    @field_validator('diagram', mode='before')
    @classmethod
    def decode_diagram(cls, value):
        # Bedrock's tool-based structured output can encode a nested object as JSON text.
        return json.loads(value) if isinstance(value, str) else value


API_CONTRACT_SECTIONS = (
    'Contract', 'Authentication and middleware', 'Processing flow',
    'State and side effects', 'Responses and examples', 'Evidence and gaps',
)
API_CONTRACT_OUTPUT = (
    'Return the completed document directly as Markdown, never JSON or a progress note. '
    'Start with a heading naming the HTTP method and path. Use these level-two headings exactly, '
    'with substantive findings under each (level-three subsections are welcome): '
    + '; '.join('## ' + heading for heading in API_CONTRACT_SECTIONS) + '. '
    'Include request/response examples, processing flow and state changes. If something is absent '
    'or unresolved, state what you inspected and what remains unknown under the relevant heading. '
    'Cite source using the graph-relative file_path:line or file_path:start-end format, including '
    'a citation covering the assigned registration line. The application saves this document only '
    'after these completion checks pass, and assembles it with the other endpoint contracts.')


def with_record(config: dict[str, Any], job: dict[str, Any], title: str) -> dict[str, Any]:
    """Return a copy of a step configuration that records its run under this documentation job."""
    return {**config, 'record': {'run_key': f"doc:{job['id']}:{uuid4().hex[:12]}", 'project_id': str(job['project_id']),
        'actor_id': str(job['requested_by']) if job.get('requested_by') else None, 'kind': 'documentation', 'title': title[:200]}}


async def generate_api_endpoint(config, service, instructions, batch, credentials, record_usage) -> GeneratedDocument:
    """Inspect one registration and accept a finished Markdown contract, not tool-loop notes."""
    model_config = copy.deepcopy(config)
    model_config['instructions'] += (
        '\nDocument only the assigned API registration. The request supplies graph locations and candidate '
        'handler symbols, not the source bodies. Read its registration and surrounding router/middleware '
        'configuration first, then locate the handler using code_graph, rg or ast-grep through shell_run. '
        'Inspect complete relevant functions with ast-grep, focused ranges with sed/read_file and graph '
        'relationships with code_graph (CodeGraph SDK search, context, callees and traverse). Follow validation, business logic, SQL/state '
        'changes and response/error mapping. Read only code relevant to this endpoint; do not dump entire '
        'repositories or graph databases. Keep concise evidence notes in the writable scratch directory. '
        'Continue until you can write the contract; do not return a plan or document other API registrations. '
        'Symbol-name matches are candidates, not proven relationships; verify receiver/import resolution '
        'against the source you inspect. Keep test/conditional registrations distinguishable. '
        'Describe unresolved or external definitions as evidence gaps; do not invent their behavior. '
        + API_CONTRACT_OUTPUT)
    task = json.dumps({'service': service, 'instructions': instructions,
        'evidence': {k: v for k, v in batch.items() if k not in ('excerpts', 'source_characters')}}, ensure_ascii=False)
    # A structured response format applies during tool use too; scratch JSON was being accepted
    # as the final document. Let the SDK finish with text and validate only its final answer.
    result = await agent(model_config, task, credentials=credentials,
        timeout_seconds=3600, max_turns=None, stream=False, on_usage=record_usage)
    return validate_api_contract(result.final_output, batch)


def validate_api_contract(markdown, batch) -> GeneratedDocument:
    """Check completion and registration attribution; this is not a semantic accuracy assessment."""
    if not isinstance(markdown, str) or not markdown.lstrip().startswith('# '):
        raise CodeProcessingError('The model did not return a Markdown endpoint contract.')
    # Ignore fenced examples when locating the document's actual sections and citations.
    prose = re.sub(r'^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$', '', markdown, flags=re.MULTILINE | re.DOTALL)
    parts = re.split(r'^## +([^\n]+)\n', prose, flags=re.MULTILINE)
    sections = {heading.strip(): body.strip() for heading, body in zip(parts[1::2], parts[2::2])}
    for heading in API_CONTRACT_SECTIONS:
        body = sections.get(heading, '')
        if not body or re.fullmatch(r'(?:wip|pending|todo|tbd|scratch|placeholder|noop|reading .* next)[.!\s]*', body, re.IGNORECASE):
            raise CodeProcessingError(f'The API contract is missing completed content for "{heading}".')
    for route in batch['routes']:
        method, _, path = route['name'].partition(' ')
        if not re.search(r'\b' + re.escape(method) + r'\b', prose) or path not in prose:
            raise CodeProcessingError('The API contract does not identify the assigned method and path.')
        citations = re.findall(re.escape(route['file_path']) + r':(\d+)(?:[-–](\d+))?', prose)
        try:
            line_count = len((Path(batch['path_base']) / route['file_path']).read_text(encoding='utf-8').splitlines())
        except (OSError, UnicodeError):
            raise CodeProcessingError('The API registration source is unavailable; synchronize the code and regenerate its graph.') from None
        if not any(1 <= int(start) <= route['start_line'] <= int(end or start) <= line_count for start, end in citations):
            raise CodeProcessingError('The API contract is missing a source citation covering its registration line.')
    return GeneratedDocument(title=', '.join(r['name'] for r in batch['routes']),
        description='API contract from graph-directed source inspection.', markdown=markdown.strip())


async def generate_api_document(store, job, config, graphs, credentials, record_usage) -> GeneratedDocument:
    """Locate endpoints in the graph, inspect their source with tools, and save each contract."""
    sources = job['source_snapshot']
    batches, coverage = await asyncio.to_thread(api_batches, graphs)
    context_fingerprint = hashlib.sha256(json.dumps({'version': API_EVIDENCE_VERSION, 'config': config,
        'service': {k: sources['service'][k] for k in ('id', 'name', 'description', 'service_type')},
        'instructions': sources['instructions']}, sort_keys=True).encode()).hexdigest()
    inventory = {'coverage': coverage, 'batches': [{k: v for k, v in batch.items() if k != 'excerpts'} for batch in batches],
        'fingerprint': context_fingerprint}
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.job_events(job_id,event_type,message,data) VALUES (%s,'api_inventory',%s,%s)",
            (job['id'], f'Discovered {len(batches)} API registrations; inspecting endpoint source with graph and file tools', Jsonb(inventory)))
    sections = []
    for position, batch in enumerate(batches, 1):
        fingerprint = hashlib.sha256(json.dumps({'context': context_fingerprint, 'batch': batch['key']}, sort_keys=True).encode()).hexdigest()
        with store.connect() as conn:
            # Only failed runs are resumed. An explicit regeneration after success calls the model again.
            cached = conn.execute("""SELECT e.data,e.job_id FROM ka.job_events e JOIN ka.jobs j ON j.id=e.job_id
                JOIN ka.documentation_generations g ON g.job_id=j.id
                WHERE j.project_id=%s AND j.status='failed' AND e.event_type='api_section'
                AND g.source_snapshot->>'service_id'=%s AND e.data->>'fingerprint'=%s
                AND j.created_at > COALESCE((SELECT max(done.created_at) FROM ka.jobs done
                    JOIN ka.documentation_generations previous ON previous.job_id=done.id
                    WHERE done.project_id=j.project_id AND done.status='succeeded'
                    AND previous.source_snapshot->>'service_id'=g.source_snapshot->>'service_id'
                    AND previous.prompt_snapshot @> '[{"kind":"api"}]'::jsonb), '-infinity'::timestamptz)
                ORDER BY e.id DESC LIMIT 1""", (job['project_id'], sources['service_id'], fingerprint)).fetchone()
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress',%s)",
                (job['id'], f'API endpoint {position}/{len(batches)}: ' + ', '.join(r['name'] for r in batch['routes'])))
        if cached is not None:
            section = {**cached['data'], 'reused_from': str(cached['job_id']), 'position': position}
        else:
            try:
                document = await generate_api_endpoint(with_record(config, job, f"API endpoint {position}: " + ', '.join(r['name'] for r in batch['routes'])),
                    {k: sources['service'][k] for k in ('id', 'name', 'description', 'service_type')},
                    sources['instructions'], batch, credentials, record_usage)
                section = {'fingerprint': fingerprint, 'batch_key': batch['key'], 'position': position,
                    'title': document.title, 'markdown': document.markdown,
                    'route_ids': [r['id'] for r in batch['routes']], 'reused_from': None}
            except CodeProcessingError as error:
                raise CodeProcessingError(f'API endpoint {position}/{len(batches)} did not finish its document. '
                    f'{error} Completed endpoint sections are saved; retry to continue.') from None
            except MaxTurnsExceeded:
                raise CodeProcessingError(f'API endpoint {position}/{len(batches)} stopped before completing its document. '
                    'Completed endpoint sections are saved; retry to continue.') from None
            except (ModelBehaviorError, ValidationError):
                raise CodeProcessingError(f'API endpoint {position}/{len(batches)} did not finish its document. '
                    'Completed endpoint sections are saved; retry to continue.') from None
            except TimeoutError:
                raise CodeProcessingError(f'API endpoint {position}/{len(batches)} timed out. Completed sections are saved; retry to continue.') from None
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message,data) VALUES (%s,'api_section',%s,%s)",
                (job['id'], f'Saved API endpoint {position}/{len(batches)}' + (' (reused)' if section['reused_from'] else ''), Jsonb(section)))
        sections.append(section)
    # Do not publish documents against a graph whose source changed during inspection.
    for project in sources['code_projects']:
        await asyncio.to_thread(load_code_graph, project, sources['code_graph_hashes'].get(str(project['id'])))
    markdown = '# ' + sources['service']['name'] + ' — API documentation\n\n'
    markdown += (f'{len(batches)} graph route registrations, documented in {len(sections)} independent sections. '
        'Registration paths may omit router prefixes; consult each contract. Registrations in test/conditional source are kept separate.\n\n'
        '## Scope and coverage\n\nThis inventory starts from static graph route nodes. It is not proof of the complete runtime API surface. '
        'Dynamic/generated routes, GraphQL/gRPC and unsupported frameworks may be absent. '
        'Each endpoint is inspected through targeted source searches and graph relationships. '
        'Unresolved behavior is documented within each section.\n\n')
    for project in coverage:
        markdown += f'- {project["name"]}: {project["route_count"]} route registrations; source hash `{project["source_hash"]}`. '
        markdown += f'Parse-error files: {len(project["parse_error_files"])}; missing folders: {len(project["missing_folders"])}; unresolved graph references: {project["unresolved_references"]}.\n'
    if not batches:
        markdown += '\nNo route nodes were found. This does not mean the application has no APIs. No model was called. Check graph language/framework coverage and route registrations before continuing.\n'
    else:
        markdown += '\n## Discovered registrations\n\n| Registration | Source | Section |\n| --- | --- | --- |\n'
        for i, batch in enumerate(batches, 1):
            route = batch['routes'][0]
            name = route['name'].replace('|', '\\|').replace('\n', ' ')
            source = route['file_path'].replace('|', '\\|').replace('\n', ' ')
            markdown += f'| {name} | {source}:{route["start_line"]} | [Endpoint {i}](#api-endpoint-{i}) |\n'
        for section in sections:
            markdown += f'\n\n<a id="api-endpoint-{section["position"]}"></a>\n\n## {section["title"]}\n\n' + section['markdown']
    return GeneratedDocument(title=sources['service']['name'] + ' API documentation',
        description='API contracts from graph-directed inspection of endpoint source code.', markdown=markdown)


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
            if value.workflow_id is not None:
                workflow = conn.execute('SELECT scope,service_id,environment_id FROM ka.documentation_workflows WHERE id=%s AND project_id=%s FOR UPDATE', (value.workflow_id,project)).fetchone()
                if workflow is None:
                    raise HTTPException(404, 'Documentation workflow not found')
                if workflow['scope'] != value.scope or workflow['service_id'] != value.service_id or (value.scope == 'environment' and workflow['environment_id'] != value.environment_id):
                    raise HTTPException(400, 'Use the subject belonging to this documentation workflow')
            kinds = {u.kind for u in value.units}
            if service is not None:
                if {'overview','api','messaging','code','diagram'}.intersection(kinds) and not value.code_project_ids:
                    value.code_project_ids = [UUID(p['id']) for p in service['repositories'] if not p['archived']]
                if not {str(p) for p in value.code_project_ids} <= {p['id'] for p in service['repositories'] if not p['archived']}:
                    raise HTTPException(400, 'Select code projects linked to this service')
            code_projects = conn.execute("""SELECT r.id,r.name,r.description,r.remote_url AS remote_git_url,r.current_commit,
                ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
                FROM ka.repositories r WHERE project_id=%s AND id=ANY(%s) AND archived_at IS NULL ORDER BY id""", (project,value.code_project_ids)).fetchall()
            if len(code_projects) != len(value.code_project_ids):
                raise HTTPException(400, 'Select available code projects')
            code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
            if code_projects and (not code_root.is_absolute() or '..' in code_root.parts):
                raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
            for code_project in code_projects:
                code_project['folder_paths'] = [str(code_root / path) for path in code_project['folder_paths']]
            commits = collect_source_commits(code_projects)
            for code_project in code_projects:
                code_project['current_commit'] = commits.get(str(code_project['id']))
            if any(commits.get(str(key)) != commit for key, commit in value.source_commits.items()):
                raise HTTPException(409, 'A requested source commit does not match a clean selected checkout')
            value.source_commits = {UUID(key): commit for key, commit in commits.items()}
            try:
                graphs = [load_code_graph(p, value.code_graph_hashes.get(str(p['id']))) for p in code_projects]
            except CodeProcessingError as exc:
                raise HTTPException(409, str(exc)) from None
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
            for unit in sorted(value.units, key=lambda u: ['overview','api','messaging','code','diagram','deployment','deployment_diagram','infrastructure','architecture'].index(u.kind)):
                row = conn.execute("""SELECT p.id,p.name,p.input_kind,v.id AS version_id,v.version,v.content
                    FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                    WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (unit.prompt_id, project)).fetchone()
                expected_kind = {'overview':'code','api':'code','messaging':'code','code':'code','diagram':'code','deployment':'server','deployment_diagram':'server','architecture':'mixed','infrastructure':'server'}[unit.kind]
                if row is None or row['input_kind'] not in (expected_kind,'mixed'):
                    raise HTTPException(400, 'Select an active prompt matching this documentation step')
                if row['version_id'] != unit.prompt_version_id:
                    raise HTTPException(409, 'An active prompt changed. Reload it before generating documentation.')
                row['active_content'] = row['content']
                row['content'] = unit.prompt_override if unit.prompt_override is not None else row['content']
                row['overridden'] = row['content'] != row['active_content']
                prompts.append({**row, 'kind': unit.kind})
                # Fixed workflow specialists, never registered as configurable AI agent profiles.
                reads_code = unit.kind in ('overview','api','messaging','code','diagram') and bool(code_projects)
                uses_ssh = unit.kind in ('deployment','deployment_diagram','infrastructure')
                configs[unit.kind] = jsonable_encoder({
                    'id': f'documentation-{unit.kind}', 'name': f'Documentation: {unit.kind}',
                    'description': 'Built-in documentation step', 'enabled': True, 'model': model,
                    'system_prompt': {'content': row['content']}, 'instructions': '',
                    'code': {'enabled': reads_code, 'projects': code_projects if reads_code else [], 'graphs': graphs if reads_code else []},
                    'ssh': {'enabled': uses_ssh, 'servers': servers if uses_ssh else []},
                    'documents': {'enabled': False, 'folders': []}, 'mcp': {'enabled': False, 'servers': []},
                    'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []},
                })
                subject_asset = environment if value.scope == 'environment' else service
                slug = re.sub(r'[^a-z0-9]+','-',subject_asset['name'].lower()).strip('-')[:40] or value.scope
                slug += '-' + str(subject_asset['id'])[:8] + '-' + unit.kind.replace('_', '-')
                if unit.kind in ('deployment','deployment_diagram'):
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
            if value.workflow_id is not None:
                conn.execute('INSERT INTO ka.documentation_workflow_jobs(workflow_id,job_id,project_id) VALUES (%s,%s,%s)', (value.workflow_id,job_id,project))
                conn.execute('UPDATE ka.documentation_workflows SET updated_at=now() WHERE id=%s', (value.workflow_id,))
            return {'job_id': job_id}

    return router


async def generate_documents(store: RepositoryStore, job: dict) -> list[tuple[dict, GeneratedDocument]]:
    sources = job['source_snapshot']
    commits = sources.get('source_commits', {})
    current_commits = collect_source_commits(sources['code_projects']) if commits else {}
    if any(current_commits.get(key) != commit for key, commit in commits.items()):
        raise ValueError('Source commits changed after this generation was requested')
    if sources.get('workflow_version') != 2:
        raise HTTPException(409, 'This request uses the old generation workflow. Submit it again with the new documentation form.')
    configs = job['agent_snapshot']
    results = []
    code_analysis = ''
    deployment_findings = ''
    graphs = None
    infrastructure_findings = None
    for target in sources['targets']:
        kind = target['kind']
        phased_code = kind in ('overview', 'api', 'messaging')
        phase_name = {'overview': 'application overview', 'api': 'API documentation', 'messaging': 'messaging documentation'}.get(kind, kind)
        step = 'writing' if kind == 'api' else 'inspection' if phased_code else 'generation'
        usage, calls, reported_calls = Usage(), 0, 0

        async def record_usage(model_usage: Usage | None):
            nonlocal calls, reported_calls
            if model_usage is not None:
                usage.add(model_usage)
                calls += 1
                reported_calls += int(model_usage.input_tokens > 0 or model_usage.output_tokens > 0 or model_usage.total_tokens > 0)
            data = {**jsonable_encoder(usage, exclude={'request_usage_entries'}),
                    'requests': calls, 'reported_calls': reported_calls, 'phase': kind, 'step': step}
            with store.connect() as conn:
                # A phase with many endpoints may outlive its initial lease while still making progress.
                if job.get('lease_token') is not None:
                    conn.execute("""UPDATE ka.jobs SET lease_expires_at=now()+interval '90 minutes'
                        WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now()""",
                        (job['id'], job['lease_token']))
                conn.execute("INSERT INTO ka.job_events(job_id,event_type,message,data) VALUES (%s,'token_usage','Recorded model token usage',%s)",
                             (job['id'], Jsonb(data)))

        await record_usage(None)
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress',%s)", (job['id'], f'Generating {kind} documentation'))
        config = copy.deepcopy(configs[kind])
        if kind in ('overview','api','messaging','code','diagram'):
            if graphs is None:
                with store.connect() as conn:
                    conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress','Verifying the selected Phase 1 graph')", (job['id'],))
                graphs = [await asyncio.to_thread(load_code_graph, project, sources.get('code_graph_hashes', {}).get(str(project['id']))) for project in sources['code_projects']]
            config['code']['graphs'] = graphs
            config['code']['projects'] = [{**p, 'folder_paths': [folder for folder in p['folder_paths']
                if folder not in {str(PurePosixPath(os.environ['CODE_ROOT']) / missing) for g in graphs if g['id'] == p['id'] for missing in g['missing_folders']}]}
                for p in config['code']['projects']]
            if not any(g['source_file_count'] for g in graphs):
                document = GeneratedDocument(title=sources['service']['name'] + (' ' + phase_name if phased_code else ' code analysis' if kind == 'code' else ' code component diagram'),
                    description='Code is not available for this service.',
                    markdown='# Code is not available\n\nNo readable source files are available in the code projects associated with this service. '
                        'The analysis prompt was skipped. Link or synchronize the source folders and generate again.\n')
                with store.connect() as conn:
                    conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress',%s)", (job['id'], f'Skipped {kind}: code is not available'))
                results.append((target,document))
                continue
        if kind == 'api':
            document = await generate_api_document(store, job, config, graphs, load_credentials(store, config), record_usage)
            DocumentInput(slug=target['slug'], title=document.title, description=document.description, markdown=document.markdown)
            results.append((target, document))
            continue
        unit_sources = {k: v for k, v in sources.items() if k not in ('targets','units','service','supporting_documents')}
        if kind in ('overview','api','messaging','code','diagram'):
            service = copy.deepcopy(sources['service'])
            service['deployments'] = []
            unit_sources.update(environment=None, environment_id=None, server_ids=[], services=[service])
        elif kind in ('deployment','deployment_diagram','infrastructure'):
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
            # Keep the graph available to verify the analysis and choose meaningful components.
            config['instructions'] += '\nUse the supplied code analysis, verify component connections against code_graph, and avoid repeating the full analysis.'
        if kind == 'deployment_diagram' and deployment_findings:
            config['ssh'] = {'enabled': False, 'servers': []}
        if phased_code:
            # Only resource references enter the task. File and graph tools retrieve evidence.
            unit_sources = {'service': {k: sources['service'][k] for k in ('id','name','description','service_type')},
                'code_projects': sources['code_projects'], 'code_graph_hashes': sources['code_graph_hashes'],
                'instructions': sources['instructions']}
            # Keep provider structured output out of source exploration: Bedrock
            # can otherwise emit its synthetic JSON tool before reading evidence.
            config['instructions'] += (
                '\nFirst collect evidence for the application overview. This is a focused analysis: '
                'aim for 10–20 focused tool calls, then write the overview from that evidence. '
                'Do not enumerate every file or endpoint. Record remaining gaps instead of continuing an exhaustive investigation. '
                'For this inspection step return evidence notes with source references. '
                'The final structured document will be written from this conversation in the next step.'
                if kind == 'overview' else
                '\nFirst inventory all producers, subscriptions, consumers and queue workers in the selected source. '
                'Trace each message contract through routing, validation, business handlers, database changes, outgoing '
                'messages and acknowledgement/retry paths. Inspect wrappers, dynamic destination expressions and failure '
                'branches. Paginate graph and file results. Do not infer messaging behavior from dependency names. '
                'Return detailed evidence notes with source references and an inventory of uninspected or unresolved '
                'paths. If none are found, record the search scope and evidence. '
                'The final structured messaging document will be written from this conversation in the next step.')
        task = json.dumps({'sources': unit_sources, 'existing_document': None if phased_code else target['existing'],
                           'code_analysis': code_analysis, 'deployment_findings': deployment_findings,
                           'observation_time': datetime.now(timezone.utc).isoformat(),
                           'document_kind': kind}, ensure_ascii=False)
        credentials = load_credentials(store, config)
        if phased_code:
            with store.connect() as conn:
                conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress','Analyzing the application with source and graph tools')", (job['id'],))
        # Use complete responses: Bedrock streaming can drop long JSON fields.
        try:
            result = await agent(with_record(config, job, f'{kind}: ' + ('source inspection' if phased_code else 'generation')), task,
                                 credentials=credentials, timeout_seconds=1200 if kind in ('code','messaging') else 600,
                                 max_turns=20 if kind == 'overview' else 40 if kind == 'messaging' else 60 if kind == 'code' else 30,
                                 max_output_tokens=16384, stream=False,
                                 on_usage=record_usage,
                                 output_type=None if phased_code else ComponentGraph if kind in ('diagram','deployment_diagram') else GeneratedDocument)
        except MaxTurnsExceeded as exc:
            if not phased_code or exc.run_data is None:
                raise
            history = ItemHelpers.input_to_new_input_list(exc.run_data.input)
            history.extend(item.to_input_item() for item in exc.run_data.new_items)
        else:
            if phased_code:
                history = result.to_input_list()
        if phased_code:
            # Same evidence and model, with source tools closed and a typed final output.
            config['instructions'] = f'Source inspection is complete. Write the final {phase_name} from the collected evidence.'
            config['code']['enabled'] = False
            step = 'writing'
            usage, calls, reported_calls = Usage(), 0, 0
            await record_usage(None)
            with store.connect() as conn:
                conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'progress',%s)", (job['id'], f'Writing {phase_name} from the collected source and graph evidence'))
            result = await agent(with_record(config, job, f'{kind}: writing'),
                'Use the source and graph evidence already in this conversation. '
                f'Write the final {phase_name}, including an explicit coverage and remaining gaps section. '
                'Do not request further tools or invent unverified details.',
                credentials=credentials, history=history, max_turns=1, max_output_tokens=16384,
                timeout_seconds=300, stream=False, output_type=CodeDocumentation, on_usage=record_usage)
        if kind in ('diagram','deployment_diagram'):
            graph = ComponentGraph.model_validate(result.final_output)
            label = 'code component architecture' if kind == 'diagram' else 'deployment architecture — ' + sources['environment']['name']
            document = GeneratedDocument(title=sources['service']['name'] + ' ' + label,
                description='Service components and their relationships.',
                markdown='# ' + label.capitalize() + '\n\n' + graph.explanation + '\n\n```d2\n' + graph_source(graph) + '```\n')
        elif phased_code:
            analysis = CodeDocumentation.model_validate(result.final_output)
            document = GeneratedDocument(**analysis.model_dump(exclude={'diagram'}))
            if analysis.diagram is not None:
                document.markdown += '\n\n## ' + {'overview': 'Component overview', 'api': 'API request flow', 'messaging': 'Message processing flow'}[kind] + '\n\n' + analysis.diagram.explanation + '\n\n```d2\n' + graph_source(analysis.diagram) + '```\n'
            # Do not publish code documentation against a graph whose source changed during analysis.
            for project in sources['code_projects']:
                await asyncio.to_thread(load_code_graph, project, sources['code_graph_hashes'].get(str(project['id'])))
        else:
            document = GeneratedDocument.model_validate(result.final_output)
            if kind == 'code':
                code_analysis = document.markdown
            elif kind == 'deployment':
                deployment_findings = document.markdown
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
            lease_token=%s,lease_expires_at=now()+interval '90 minutes' WHERE id=%s""", (socket.gethostname(), lease, job['id']))
        job['lease_token'] = lease
    try:
        results = asyncio.run(generate_documents(store, job))
        sources = job['source_snapshot']
        commits = sources.get('source_commits', {})
        current_commits = collect_source_commits(sources['code_projects']) if commits else {}
        if any(current_commits.get(key) != commit for key, commit in commits.items()):
            raise ValueError('Source commits changed during documentation generation')
        exports = []
        with store.connect() as conn:
            active = conn.execute("SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() FOR UPDATE", (job['id'], lease)).fetchone()
            if active is None:
                return True
            saved = []
            for target, document in results:
                shared = target['kind'] in ('overview','api','messaging','code','diagram')
                architecture = target['kind'] == 'architecture'
                infrastructure = target['kind'] == 'infrastructure'
                linked_services = sources['services']
                if infrastructure:
                    linked_services = [s for s in linked_services if any(d['server_id'] in sources['server_ids'] for d in s['deployments'])]
                existing = target['existing']
                value = DocumentInput(slug=target['slug'], title=document.title, description=document.description,
                    markdown=document.markdown, purpose='diagram' if target['kind'] == 'deployment_diagram' else target['kind'],
                    service_ids=[s['id'] for s in linked_services] if architecture or infrastructure else [sources['service_id']],
                    code_project_ids=list({p['id'] for s in linked_services for p in s['repositories'] if not p['archived']}) if architecture or infrastructure else sources['code_project_ids'],
                    source_commits=commits if shared else {},
                    deployment_ids=[d['id'] for s in linked_services for d in s['deployments'] if d['server_id'] in sources['server_ids']] if target['kind'] in ('deployment','deployment_diagram','infrastructure') else [],
                    environment_ids=[] if shared else [sources['environment_id']],
                    server_ids=[s['id'] for s in sources['environment']['servers']] if architecture else sources['server_ids'] if target['kind'] in ('deployment','deployment_diagram','infrastructure') else [],
                    revision=existing['revision'] if existing is not None else None)
                saved.append(save_document(conn, job['project_id'], job['requested_by'], value,
                    UUID(existing['id']) if existing is not None else None, job['id']))
                exports.append({'slug': value.slug, 'revision': saved[-1]['revision'], 'markdown': value.markdown,
                                'source_commits': {str(key): commit for key, commit in (value.source_commits or {}).items()}})
            folders = []
            export_error = None
            phase_metadata = None
            if sources['targets'][0]['kind'] in ('overview', 'api', 'messaging'):
                prompt = job['prompt_snapshot'][0]
                phase_metadata = jsonable_encoder({'phase': sources['targets'][0]['kind'], 'service_id': sources['service_id'],
                    'service_name': sources['service']['name'], 'job_id': job['id'], 'document_id': saved[0]['id'],
                    'version_id': saved[0]['version_id'], 'version': saved[0]['revision'],
                    'code_graph_hashes': sources['code_graph_hashes'], 'code_projects': sources['code_projects'],
                    'source_commits': commits,
                    'prompt_id': prompt['id'], 'prompt_version_id': prompt['version_id'], 'prompt_version': prompt['version'],
                    'prompt_overridden': prompt['overridden'], 'prompt_hash': hashlib.sha256(prompt['content'].encode()).hexdigest(),
                    'generated_at': datetime.now(timezone.utc).isoformat()})
            try:
                for document in exports:
                    folders.append(export_document(document, phase_metadata=phase_metadata))
            except (OSError, ValueError):
                export_error = 'Documents were saved. File export failed; retry Export to folder from the document page.'
            conn.execute("""UPDATE ka.jobs SET status='succeeded',result=%s,finished_at=now(),worker_id=NULL,
                lease_token=NULL,lease_expires_at=NULL WHERE id=%s""",
                (Jsonb(jsonable_encoder({'documents': saved, 'output_folders': folders, 'export_error': export_error,
                    'phase_metadata': phase_metadata})), job['id']))
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message) VALUES (%s,'succeeded','Documents saved for review')", (job['id'],))
    except Exception as exc:
        # Provider/tool errors can contain request bodies or secrets. Never copy those to jobs or logs.
        error = 'Documentation generation failed. Check the model, selected resources and documentation prompts, then retry.'
        if isinstance(exc, HTTPException):
            error = str(exc.detail)
        elif isinstance(exc, CodeProcessingError):
            error = str(exc)
        elif isinstance(exc, MaxTurnsExceeded):
            error = 'The agent reached its analysis turn limit before producing a document. Narrow the prompt or selected code projects and retry.'
        elif isinstance(exc, TimeoutError):
            error = 'Documentation analysis timed out. Narrow the prompt or selected resources and retry.'
        elif isinstance(exc, (ValidationError, ModelBehaviorError)):
            error = 'The model returned an incomplete or invalid document. Review the prompt and retry.'
        elif type(exc).__name__ in ('AuthenticationError', 'PermissionDeniedError'):
            error = 'The model provider rejected authentication or access. Check this model connection in LLM Settings.'
        elif type(exc).__name__ == 'RateLimitError':
            error = 'The model provider rate limit was reached. Retry after its limit resets; completed API sections are saved.'
        elif type(exc).__name__ in ('APIConnectionError', 'APITimeoutError'):
            error = 'The model provider could not be reached or timed out. Completed API sections are saved; retry this phase.'
        elif type(exc).__name__ == 'BadRequestError':
            error = 'The model provider rejected the request. Check model limits and settings; completed API sections are saved.'
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.job_events(job_id,event_type,message,data) VALUES (%s,'failure',%s,%s)",
                (job['id'], error, Jsonb({'error_type': type(exc).__name__})))
            conn.execute("""UPDATE ka.jobs SET status='failed',error=%s,finished_at=now(),worker_id=NULL,
                lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND lease_token=%s AND status='running'""", (error, job['id'], lease))
    return True


def main() -> None:
    from knowledge_admin.agent_tracing import setup
    setup()
    store = RepositoryStore('', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL'])
    from knowledge_admin import run_record
    run_record.configure(store, None)
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
