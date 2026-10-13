"""Generate documents' form API until part 3: the live context and the create of an ad-hoc native agent session.
The run operations and lists are in sessions/api.py. Turns run in agent-session-worker."""
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.data_root import documentation_folder
from knowledge_admin.documents import document_slug
from knowledge_admin.generate_documents import resource_context, subjects
from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.session_text import FINAL_ANSWER
from knowledge_admin.sessions.rows import NO_MODEL, SELECTION, Selection, session_detail

NOTHING_CHOSEN = 'Choose at least one code project, collection, environment, service or server to document.'


class SessionInput(Selection):
    """Docs → Generate documents → New: an ad-hoc native agent with this model and system prompt, the context the form
    generated (or the user edited), the prompt as its first message, and the document's name and folder."""
    model_id: UUID
    system_prompt: str = Field(max_length=20000)
    context: str = Field(default='', max_length=100000)
    prompt: str = Field(max_length=100000)
    prompt_id: UUID | None = None
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        texts = ('system_prompt', 'context', 'prompt', 'document_name')
        if any('\x00' in getattr(self, name) for name in texts):
            raise ValueError('Text cannot contain null characters')
        for name in texts:
            setattr(self, name, getattr(self, name).strip())
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        if not self.prompt:
            raise ValueError('Enter a prompt')
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        if not any(getattr(self, name) for name in SELECTION):
            raise ValueError(NOTHING_CHOSEN)
        return self


def run_folders(config: dict) -> list[dict]:
    """The folders of the run's code projects, each once (chosen first, then brought by environments and services)."""
    folders, seen = [], set()
    for item in config['code']['projects']:
        for path in item['folder_paths']:
            if path not in seen:
                seen.add(path)
                folders.append({'path': path, 'kind': 'code_project', 'name': item['name'], 'code_project_id': item['id']})
    return folders


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/generations')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Generate documents storage is not configured.')
        return store

    def audit(conn, actor, action: str, resource_type: str, details: dict, resource_id=None) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)''', (store.project(conn, app_name), actor, action, resource_type, resource_id, Jsonb(details)))

    @router.post('/context')
    def generation_context(value: Selection) -> dict:
        """The form's live context: what the selection says, as the ad-hoc agent will read it."""
        db = database()
        with db.connect() as conn:
            return {'text': resource_context(subjects(conn, db.project(conn, app_name), value))}

    @router.post('/sessions', status_code=201)
    def create_session(value: SessionInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            model = load_model(conn, value.model_id)
            if model is None or not model['enabled'] or not model_fits(model):
                raise HTTPException(400, NO_MODEL)
            described = subjects(conn, project, value)  # unknown ids: 400 naming the kind
            system_prompt = '\n\n'.join(part for part in (value.system_prompt, value.context) if part)
            try:
                pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=value)
            except RunResourceError as exc:
                raise HTTPException(400, str(exc)) from None
            context = {**described, 'folders': run_folders(pinned),
                       'code_project_ids': [item['id'] for item in described['code_projects']]}
            snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
            session_id = uuid4()
            # The worker decrypts per turn: the pinned config holds credential references only.
            conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder,agent_config,run_resources)
                VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, actor, model['id'], value.document_name, Jsonb(jsonable_encoder(snapshot)),
                 Jsonb(context), value.document_name, value.folder, Jsonb(jsonable_encoder(pinned)),
                 Jsonb(jsonable_encoder(pinned['run_resources']))))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, f'{value.prompt}\n\n{FINAL_ANSWER}'))
            audit(conn, actor, 'generation.session.created', 'agent_session',
                  jsonable_encoder({'ephemeral': True, 'model_id': model['id'],
                                    'prompt_id': value.prompt_id}), session_id)
            return session_detail(conn, project, session_id)

    return router
