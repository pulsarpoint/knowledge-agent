"""One parent task, a coordinating agent, and results from its selected subagents."""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agents import RunConfig, RunHooks
from agents.usage import Usage
from typing import Any
from uuid import UUID, uuid4

import psycopg
from knowledge_llm import decode_model
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.agent_activity import SdkActivity, redact
from knowledge_admin.agents import create_agent, get_agent_config, load_credentials
from knowledge_admin.knowledge_agents.config import require_native
from knowledge_admin.run_record import CompositeHooks, RunRecorder
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.coordinator import coordinator
from knowledge_admin.model_adapter import agent_model


FIELDS = '''id,agent_id,agent_name,task,status,cancel_requested,model_name,prompt_version,
    result,error,usage,created_at,started_at,finished_at,parent_id,phase,plan,trace_id,span_id'''
logger = logging.getLogger(__name__)
COORDINATOR_SETTINGS = '''SELECT s.*,a.name,a.description,a.enabled,a.model_id,a.revision AS agent_revision,
    v.content AS system_prompt,v.version AS prompt_version,p.revision AS prompt_revision,
    m.name AS model_name,m.enabled AS model_enabled
    FROM ka.coordinator_settings s JOIN ka.agents a ON a.id=s.agent_id AND a.is_coordinator
    JOIN ka.agent_prompts p ON p.agent=a.id JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id
    LEFT JOIN ka.llm_models m ON m.id=a.model_id'''


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: UUID
    task: str = Field(min_length=1, max_length=40000)
    subagent_ids: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode='after')
    def team(self):
        if len(set(self.subagent_ids)) != len(self.subagent_ids):
            raise ValueError('Select each subagent only once.')
        return self


class CoordinatorInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int = Field(ge=1, strict=True)
    notes: str = Field(max_length=40000)
    summary_prompt: str = Field(min_length=1, max_length=40000)
    max_turns: int = Field(ge=1, le=30, strict=True)
    max_subtasks: int = Field(ge=1, le=50, strict=True)
    timeout_seconds: int = Field(ge=30, le=3600, strict=True)



class SubagentActivity(RunHooks):
    """Each nested run publishes through the activity object in its private context."""

    async def on_agent_start(self, context, agent):
        await context.context['activity'].on_agent_start(context, agent)

    async def on_tool_start(self, context, agent, tool):
        await context.context['activity'].on_tool_start(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        await context.context['activity'].on_tool_end(context, agent, tool, result)


async def execute_task(store: RepositoryStore, task: dict[str, Any], slots: asyncio.Semaphore) -> None:
    """Load the team, persist task lifecycle, and call the SDK coordinator."""
    credentials, secrets, active = [], [], set()
    recorder = RunRecorder(run_key=f"board:{task['id']}", project_id=None, agent_id='coordinator',
                           actor_id=str(task['created_by']) if task.get('created_by') else None, secrets=secrets)
    recorder.add('start', {'kind': 'board', 'title': task['task'][:200], 'agent': 'Coordinator'})
    recorder.add('input', {'task': task['task'], 'history': []})
    status = 'failed'

    async def on_activity(value):
        value = {'task_id': str(task['id']), **value,
            'title': redact(value['title'], secrets),
            'detail': redact(value['detail'], secrets, partial=value['status'] == 'inProgress')}
        while len(json.dumps(value).encode()) > 7500:
            value['detail'] = value['detail'][:len(value['detail']) // 2]
        try:
            with store.connect() as conn:
                if value.get('trace_id'):
                    conn.execute('''UPDATE ka.agent_board_tasks SET trace_id=%s,span_id=coalesce(span_id,%s)
                        WHERE id=%s''', (value['trace_id'], value['span_id'], value['task_id']))
                conn.execute('SELECT pg_notify(%s,%s)', ('ka_board_' + task['id'].hex, json.dumps(value)))
        except psycopg.Error:
            logger.warning('Live board activity could not be delivered.')

    try:
        with store.connect() as conn:
            settings = task['config'] or conn.execute(COORDINATOR_SETTINGS).fetchone()
            if not conn.execute('SELECT 1 FROM ka.agents WHERE is_coordinator AND enabled').fetchone():
                raise ValueError('The coordinator is disabled.')
            row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s AND enabled', (settings['model_id'],)).fetchone()
            if row is None:
                raise ValueError('The coordinator model is unavailable.')
            model = decode_model(row, store.cipher)
            team = conn.execute('SELECT agent_id FROM ka.agent_board_members WHERE task_id=%s ORDER BY agent_id', (task['id'],)).fetchall()
            # Queued tasks from before the summary step was introduced have no summary prompt snapshot.
            summary_prompt = settings.get('summary_prompt') or conn.execute(COORDINATOR_SETTINGS).fetchone()['summary_prompt']
            snapshot = {**settings, 'summary_prompt': summary_prompt, 'runtime': 'agents_sdk', 'model': {key: row[key] for key in
                ('id','name','provider','model','api_format','base_url','aws_region','revision')}}
            conn.execute('''UPDATE ka.agent_board_tasks SET config=%s,model_name=%s,prompt_version=%s WHERE id=%s''',
                (Jsonb(jsonable_encoder(snapshot)), model.name, settings.get('prompt_version'), task['id']))
        if model.api_key:
            secrets.append(model.api_key)
        calls = 0

        def as_board_tool(config, assistant, keys):
            activities = {}

            async def stream(payload):
                await activities[payload['tool_call'].call_id].notify(payload['event'])

            async def output(result):
                return redact(str(result.final_output), secrets)

            tool = assistant.as_tool(tool_name='delegate_' + config['id'],
                tool_description=f"{config['name']}: {config['description']}\nWhen to use: {config['when_to_use']}",
                on_stream=stream, hooks=CompositeHooks(SubagentActivity(), recorder), custom_output_extractor=output, max_turns=20,
                run_config=RunConfig(tracing={'include_task_and_turn_spans': False}), failure_error_function=None)
            sdk_invoke = tool.on_invoke_tool

            async def invoke(tool_context, arguments):
                # This boundary owns DB state and access; as_tool owns the nested SDK run.
                nonlocal calls
                try:
                    assignment = json.loads(arguments)['input']
                    if not isinstance(assignment, str) or not assignment.strip() or len(assignment) > 40000:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return json.dumps({'status': 'rejected', 'error': 'Provide an assignment of 1 to 40,000 characters.'})
                if calls >= settings['max_subtasks']:
                    return json.dumps({'status': 'rejected', 'error': 'The subtask limit is reached. Summarize available results and missing work.'})
                calls += 1
                assignment = redact(assignment.strip(), secrets)
                with store.connect() as conn:
                    parent = conn.execute('SELECT cancel_requested,trace_id FROM ka.agent_board_tasks WHERE id=%s FOR UPDATE', (task['id'],)).fetchone()
                    if parent['cancel_requested']:
                        raise asyncio.CancelledError
                    child_id = uuid4()
                    conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,parent_id,created_by,config,model_name,prompt_version,trace_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (child_id, config['id'], config['name'], assignment,
                        task['id'], task['created_by'], Jsonb(config), config['model']['name'], config['system_prompt']['version'], parent['trace_id']))
                    conn.execute("UPDATE ka.agent_board_tasks SET phase='working' WHERE id=%s", (task['id'],))
                current_task = asyncio.current_task()
                active.add(current_task)
                status, answer, error, usage = 'failed', None, None, {}

                async def child_activity(value):
                    await on_activity({**value, 'task_id': str(child_id)})

                activity = SdkActivity(secrets, child_activity)
                activities[tool_context.tool_call_id] = activity
                event = {'id': 'delegation-' + str(child_id), 'title': 'Delegate to ' + config['name'],
                    'detail': assignment[:4000], 'kind': 'delegation', 'status': 'inProgress', 'time': datetime.now(UTC).isoformat()}
                try:
                    await on_activity(event)
                    async with slots, asyncio.timeout(300):
                        current_keys = await asyncio.to_thread(load_credentials, store, config)
                        changed = current_keys != keys
                        current_keys.clear()
                        if changed:
                            raise ValueError('Credentials changed. Start a new board task.')
                        with store.connect() as conn:
                            current = conn.execute('''UPDATE ka.agent_board_tasks SET status='running',started_at=now()
                                WHERE id=%s AND NOT cancel_requested RETURNING id''', (child_id,)).fetchone()
                        if current is None:
                            raise asyncio.CancelledError
                        with TemporaryDirectory(prefix='knowledge-subagent-', dir=os.environ.get('AGENT_WORKSPACE_ROOT')) as directory:
                            if os.environ.get('AGENT_SHELL_URL') and os.geteuid() == 0:
                                os.chown(directory, 1000, 1000)
                            context = replace(tool_context, usage=Usage(), context={'config': config,
                                'credentials': keys, 'workspace': Path(directory).resolve(),
                                'shell_enabled': config['code']['enabled'] or config['documents']['enabled'], 'activity': activity})
                            prompt = json.dumps({'main_task': task['task'], 'assigned_subtask': assignment}, ensure_ascii=False)
                            answer = await sdk_invoke(context, json.dumps({'input': prompt}))
                            usage = jsonable_encoder(context.usage)
                    status = 'completed'
                except asyncio.CancelledError:
                    with store.connect() as conn:
                        cancelled = conn.execute('SELECT cancel_requested FROM ka.agent_board_tasks WHERE id=%s', (task['id'],)).fetchone()['cancel_requested']
                    status = 'cancelled' if cancelled else 'failed'
                    error = None if cancelled else 'Execution was interrupted. Review before running again.'
                    raise
                except Exception as exc:
                    error = 'The subtask reached its time limit.' if isinstance(exc, TimeoutError) else 'Agent execution failed. Check its model, resources and connection settings.'
                    logger.warning('Board subtask %s failed (%s)', child_id, type(exc).__name__)
                finally:
                    activity.close()
                    activities.pop(tool_context.tool_call_id, None)
                    active.discard(current_task)
                    with store.connect() as conn:
                        conn.execute('''UPDATE ka.agent_board_tasks SET status=%s,phase='done',result=%s,error=%s,usage=%s,finished_at=now()
                            WHERE id=%s''', (status, answer, error, Jsonb(usage), child_id))
                        conn.execute('''UPDATE ka.agent_board_tasks SET phase='reviewing' WHERE id=%s
                            AND NOT EXISTS (SELECT 1 FROM ka.agent_board_tasks WHERE parent_id=%s AND status IN ('queued','running'))''', (task['id'], task['id']))
                    await on_activity({**event, 'status': status, 'detail': (answer or error or 'Cancelled.')[:4000]})
                return redact(json.dumps({'id': str(child_id), 'agent_id': config['id'], 'task': assignment,
                    'status': status, 'result': answer, 'error': error}, ensure_ascii=False), secrets)

            tool.on_invoke_tool = invoke
            return tool

        async with asyncio.timeout(settings['timeout_seconds']), AsyncExitStack() as connections:
            tools = []
            for member in team:
                config = await asyncio.to_thread(get_agent_config, member['agent_id'], conninfo=store.conninfo)
                require_native(config)
                keys = await asyncio.to_thread(load_credentials, store, config)
                credentials.append(keys)
                secrets.extend(keys.values())
                assistant = await connections.enter_async_context(create_agent(config, credentials=keys, timeout_seconds=300))
                tools.append(as_board_tool(config, assistant, keys))
            sdk_model, options = await connections.enter_async_context(agent_model(row, model.api_key, settings['timeout_seconds']))
            prompt = settings['system_prompt'] + '\n\n' + settings.get('notes', '')
            try:
                result = await coordinator(model=sdk_model, model_settings=options, system_prompt=prompt,
                    summary_prompt=summary_prompt, task=task['task'], tools=tools, on_activity=on_activity,
                    secrets=secrets, max_turns=settings['max_turns'], max_subtasks=settings['max_subtasks'], timeout_seconds=settings['timeout_seconds'],
                    recorder=recorder)
            finally:
                remaining = tuple(active)
                for running in remaining:
                    running.cancel()
                await asyncio.gather(*remaining, return_exceptions=True)
        with store.connect() as conn:
            conn.execute('''UPDATE ka.agent_board_tasks SET status='completed',phase='done',result=%s,usage=%s,finished_at=now()
                WHERE id=%s AND status='running' ''', (redact(str(result.final_output), secrets), Jsonb(jsonable_encoder(result.context_wrapper.usage)), task['id']))
        status = 'completed'
    except asyncio.CancelledError:
        status = 'cancelled'
        with store.connect() as conn:
            conn.execute('''UPDATE ka.agent_board_tasks SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
                error=CASE WHEN cancel_requested THEN NULL ELSE 'Execution was interrupted. Review before running again.' END,
                finished_at=now() WHERE id=%s AND status IN ('queued','running') ''', (task['id'],))
        raise
    except Exception as exc:
        error = 'Agent execution failed. Check its model, resources and connection settings.'
        if isinstance(exc, TimeoutError):
            error = 'The task reached its time limit. Review the available subagent results.'
        elif isinstance(exc, (ValueError, LookupError, InvalidTag)):
            error = 'The agent configuration or credentials are unavailable. Check its settings.'
        logger.warning('Board task %s failed (%s)', task['id'], type(exc).__name__)
        recorder.add('error', {'type': type(exc).__name__, 'message': str(exc)})
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_board_tasks SET status='failed',error=%s,finished_at=now() WHERE id=%s AND status IN ('queued','running')", (error, task['id']))
    finally:
        recorder.add('end', {'status': status})
        try:
            await asyncio.shield(asyncio.to_thread(recorder.flush))
        except Exception:
            logger.warning('Board task %s record could not be saved.', task['id'])
        for keys in credentials:
            keys.clear()
        secrets.clear()


async def run_board(store: RepositoryStore) -> None:
    """One dispatcher across API processes, with three coordinators and three specialist slots."""
    active: dict[UUID, asyncio.Task] = {}
    slots = asyncio.Semaphore(3)
    while True:
        try:
            # The session lock elects one dispatcher. Closing the connection or
            # losing the process releases it, allowing another process to recover.
            with store.connect() as conn:
                conn.autocommit = True
                if not conn.execute('SELECT pg_try_advisory_lock(27001,0) AS acquired').fetchone()['acquired']:
                    await asyncio.sleep(1)
                    continue
                conn.execute('''UPDATE ka.agent_board_tasks SET status='failed',finished_at=now(),
                    error='Execution was interrupted. Review before running again.' WHERE status='running' ''')
                conn.execute('''UPDATE ka.agent_board_tasks SET status='cancelled',finished_at=now(),cancel_requested=true
                    WHERE parent_id IS NOT NULL AND status='queued' ''')
                while True:
                    # This also checks that the connection owning the lock is alive.
                    cancelling = conn.execute("SELECT id FROM ka.agent_board_tasks WHERE parent_id IS NULL AND status='running' AND cancel_requested").fetchall()
                    for row in cancelling:
                        if row['id'] in active and not active[row['id']].cancelling():
                            active[row['id']].cancel()
                    for task_id, running in list(active.items()):
                        if running.done():
                            await asyncio.gather(running, return_exceptions=True)
                            conn.execute('''UPDATE ka.agent_board_tasks
                                SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
                                    error=CASE WHEN cancel_requested THEN NULL ELSE 'Execution ended before its result could be saved.' END,
                                    finished_at=now() WHERE id=%s AND status='running' ''', (task_id,))
                            del active[task_id]
                    rows = conn.execute('''UPDATE ka.agent_board_tasks SET status='running',started_at=now()
                        WHERE id IN (SELECT id FROM ka.agent_board_tasks WHERE status='queued' AND parent_id IS NULL
                            ORDER BY created_at,id LIMIT %s FOR UPDATE SKIP LOCKED)
                        RETURNING *''', (3 - len(active),)).fetchall()
                    for row in rows:
                        active[row['id']] = asyncio.create_task(execute_task(store, row, slots))
                    await asyncio.sleep(0.5)
        except psycopg.Error as exc:
            logger.warning('Board dispatcher unavailable (%s)', type(exc).__name__)
        finally:
            for running in active.values():
                if not running.cancelling():
                    running.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
            active.clear()
        await asyncio.sleep(1)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(run_board(store)) if store is not None else None
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    router = APIRouter(prefix='/agent-board', lifespan=lifespan)

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Agent board storage is not configured.')
        return store

    @router.get('/settings')
    def settings(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            return conn.execute(COORDINATOR_SETTINGS).fetchone()

    @router.put('/settings')
    def save_settings(value: CoordinatorInput, request: Request):
        db = database()
        if '\x00' in value.notes or '\x00' in value.summary_prompt:
            raise HTTPException(400, 'Instructions cannot contain null characters.')
        with db.connect() as conn:
            current = conn.execute('SELECT revision FROM ka.coordinator_settings FOR UPDATE').fetchone()
            if current['revision'] != value.revision:
                raise HTTPException(409, 'Coordinator settings changed. Reload before saving.')
            actor = db.actor(conn, *request_actor(request))
            conn.execute('''UPDATE ka.coordinator_settings SET notes=%s,summary_prompt=%s,max_turns=%s,
                max_subtasks=%s,timeout_seconds=%s,revision=revision+1,updated_at=now(),updated_by=%s''',
                (value.notes,value.summary_prompt,value.max_turns,value.max_subtasks,value.timeout_seconds,actor))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'coordinator.updated','coordinator',%s)''',
                (db.project(conn,app_name),actor,Jsonb({'revision': value.revision+1})))
            return conn.execute(COORDINATOR_SETTINGS).fetchone()

    def with_subtasks(conn, row):
        row['subagents'] = conn.execute('''SELECT a.id,a.name FROM ka.agent_board_members m
            JOIN ka.agents a ON a.id=m.agent_id WHERE m.task_id=%s ORDER BY a.name''', (row['id'],)).fetchall()
        row['subtasks'] = conn.execute(f'''SELECT {FIELDS} FROM ka.agent_board_tasks
            WHERE parent_id=%s ORDER BY created_at,id''', (row['id'],)).fetchall()
        public_url = os.environ.get('PHOENIX_PUBLIC_URL', '').rstrip('/')
        for task in (row, *row['subtasks']):
            task['trace_url'] = f"{public_url}/redirects/spans/{task['span_id']}" if public_url and task['span_id'] else None
            if public_url and task['trace_id'] and (task['parent_id'] is None or task['status'] in ('queued', 'running')):
                task['trace_url'] = f"{public_url}/redirects/traces/{task['trace_id']}"
        return row

    @router.get('/tasks/{task_id}/activity')
    async def activity(task_id: UUID, request: Request):
        db = database()
        with db.connect() as conn:
            task = conn.execute('SELECT parent_id FROM ka.agent_board_tasks WHERE id=%s', (task_id,)).fetchone()
            if task is None:
                raise HTTPException(404, 'Board task not found.')
            if task['parent_id'] is not None:
                raise HTTPException(400, 'Subscribe to the main task for its team activity.')

        async def events():
            # LISTEN before the first status read to avoid a subscription race.
            # Closing the response releases this dedicated connection.
            try:
                async with await psycopg.AsyncConnection.connect(db.conninfo, autocommit=True, row_factory=dict_row) as conn:
                    await conn.execute(psycopg.sql.SQL('LISTEN {}').format(psycopg.sql.Identifier('ka_board_' + task_id.hex)))
                    yield 'event: connected\ndata: {}\n\n'
                    while not await request.is_disconnected():
                        async for notification in conn.notifies(timeout=1):
                            yield 'event: activity\ndata: ' + notification.payload + '\n\n'
                        cursor = await conn.execute('SELECT status FROM ka.agent_board_tasks WHERE id=%s', (task_id,))
                        row = await cursor.fetchone()
                        if row is None or row['status'] not in ('queued', 'running'):
                            yield 'event: done\ndata: {}\n\n'
                            return
                        yield ': keepalive\n\n'
            except psycopg.Error:
                yield 'event: unavailable\ndata: {}\n\n'

        return StreamingResponse(events(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
        })

    @router.get('/tasks')
    def listing(response: Response, offset: int = Query(0, ge=0)):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            rows = conn.execute(f'''SELECT {FIELDS} FROM ka.agent_board_tasks WHERE parent_id IS NULL
                ORDER BY created_at DESC,id LIMIT 21 OFFSET %s''', (offset,)).fetchall()
            return {'tasks': [with_subtasks(conn, row) for row in rows[:20]], 'has_more': len(rows) > 20}

    @router.get('/tasks/{task_id}')
    def detail(task_id: UUID, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            row = conn.execute(f'SELECT {FIELDS} FROM ka.agent_board_tasks WHERE id=%s', (task_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Board task not found.')
            return with_subtasks(conn, row)

    @router.post('/tasks', status_code=202)
    def submit(value: TaskInput, request: Request):
        db = database()
        subject, email = request_actor(request)
        with db.connect() as conn:
            # Serialize admission and duplicate request IDs without blocking runs.
            conn.execute('SELECT pg_advisory_xact_lock(27001,1)')
            actor = db.actor(conn, subject, email)
            old = conn.execute('SELECT * FROM ka.agent_board_tasks WHERE id=%s', (value.id,)).fetchone()
            if old is not None:
                previous_team = {row['agent_id'] for row in conn.execute('SELECT agent_id FROM ka.agent_board_members WHERE task_id=%s', (value.id,))}
                if (old['created_by'] != actor or old['parent_id'] is not None or old['task'] != value.task
                        or previous_team != set(value.subagent_ids)):
                    raise HTTPException(409, 'This task request ID has already been used.')
                return {'id': old['id']}
            profiles = conn.execute('''SELECT a.id,a.name,a.enabled,a.is_coordinator,a.runtime,
                CASE WHEN v.id IS NULL THEN a.resources ELSE coalesce(v.config->>'resources','fixed') END AS resources,
                m.enabled AS model_enabled,p.active_version_id
                FROM ka.agents a LEFT JOIN ka.agent_versions v ON v.id=a.active_config_version_id
                LEFT JOIN ka.llm_models m ON m.id=CASE WHEN v.id IS NULL THEN a.model_id ELSE (v.config->'model'->>'id')::uuid END
                LEFT JOIN ka.agent_prompts p ON p.agent=a.id WHERE a.id=ANY(%s) FOR SHARE OF a''',
                (value.subagent_ids,)).fetchall()
            if len(profiles) != len(value.subagent_ids):
                raise HTTPException(404, 'A selected agent was not found.')
            if any(p['is_coordinator'] for p in profiles):
                raise HTTPException(400, 'The coordinator cannot be selected as a subagent.')
            if any(p['runtime'] != 'openai' for p in profiles):
                raise HTTPException(400, 'Claude Code agents cannot join the multi-agent board.')
            if any(p['resources'] != 'fixed' for p in profiles):
                raise HTTPException(400, 'Per-run agents cannot join the multi-agent board yet.')
            if any(not p['enabled'] or not p['model_enabled'] or p['active_version_id'] is None for p in profiles):
                raise HTTPException(400, 'Choose enabled agents with models and active system prompts.')
            if conn.execute("SELECT count(*) AS n FROM ka.agent_board_tasks WHERE parent_id IS NULL AND status IN ('queued','running')").fetchone()['n'] >= 30:
                raise HTTPException(409, 'The board already has 30 unfinished main tasks. Wait for a result before adding more.')
            settings = conn.execute(COORDINATOR_SETTINGS + ' FOR SHARE OF s,a,p,v').fetchone()
            if not settings['enabled']:
                raise HTTPException(400, 'The coordinator is disabled. Enable it on its agent page before starting a task.')
            if not settings['model_enabled']:
                raise HTTPException(400, 'Select an enabled model in Coordinator settings before starting a task.')
            conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,created_by,phase,config)
                VALUES (%s,%s,%s,%s,%s,'planning',%s)''',
                (value.id, settings['agent_id'], settings['name'], value.task, actor, Jsonb(json.loads(json.dumps(settings, default=str)))))
            for agent_id in value.subagent_ids:
                conn.execute('INSERT INTO ka.agent_board_members(task_id,agent_id) VALUES (%s,%s)', (value.id, agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_board.submitted','agent_board_task',%s,%s)''',
                (db.project(conn, app_name), actor, value.id, Jsonb({'coordinator_revision': settings['revision'], 'subagent_ids': value.subagent_ids})))
            return {'id': value.id}

    @router.post('/tasks/{task_id}/cancel')
    def cancel(task_id: UUID, request: Request):
        db = database()
        subject, email = request_actor(request)
        with db.connect() as conn:
            row = conn.execute('SELECT status,parent_id FROM ka.agent_board_tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Board task not found.')
            if row['status'] not in ('queued', 'running'):
                raise HTTPException(409, 'This task has already finished.')
            if row['parent_id'] is not None:
                raise HTTPException(400, 'Stop the main task to cancel its subagents.')
            conn.execute('''UPDATE ka.agent_board_tasks SET cancel_requested=true,
                status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END WHERE id=%s''', (task_id,))
            conn.execute('''UPDATE ka.agent_board_tasks SET cancel_requested=true,
                status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END
                WHERE parent_id=%s AND status IN ('queued','running')''', (task_id,))
            actor = db.actor(conn, subject, email)
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_board.cancelled','agent_board_task',%s,'{}')''', (db.project(conn, app_name), actor, task_id))
            return {'id': task_id}

    return router
