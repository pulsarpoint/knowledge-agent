"""The Debug tab's data: GET /api/sessions/{id}/debug for a run's pinned configuration and POST /api/sessions/preview
before a run starts show the instructions and tools exactly as the runner builds them (the same code), with secrets
redacted."""

import asyncio
from uuid import uuid4

import asyncssh

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.agents.runner import create_agent, load_credentials
from knowledge_admin.sessions.start import assistant_prompt_id
from test_discovery_runs import config_of
from test_repositories import HEADERS

PLAIN = ['ssh_run', 'get_document']
MODEL_KEY = 'sk-debug-model-key-0123456789'


def ssh_key(world) -> str:
    """Set up SSH for the world's server; returns the private key."""
    server = world.server
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    current = world.client.get(f'/api/servers/{server["id"]}/ssh', headers=HEADERS).json()
    saved = world.client.put(
        f'/api/servers/{server["id"]}/ssh',
        headers=HEADERS,
        json={'revision': current['revision'], 'username': 'ops', 'port': 22, 'private_key': key, 'known_hosts': ''},
    )
    assert saved.status_code == 200, saved.text
    return key


def keyed_model(world) -> str:
    created = world.client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Keyed',
            'provider': 'openai',
            'model': 'gpt-5',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
            'api_key': MODEL_KEY,
        },
    )
    assert created.status_code in (200, 201), created.text
    return created.json()['id']


def body(world, **change) -> dict:
    return {
        'started_from': {'server_id': world.server['id']},
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        'tools': PLAIN,
        **change,
    }


def start(world, **change) -> str:
    created = world.client.post('/api/sessions', headers=HEADERS, json=body(world, **change))
    assert created.status_code == 201, created.text
    return created.json()['id']


def debug(world, session_id) -> dict:
    response = world.client.get(f'/api/sessions/{session_id}/debug', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def runner_view(world, session_id) -> tuple[str, list[dict]]:
    """What the runner gives the model for a turn of this run: the native session turn's own call path."""
    config = config_of(world, session_id)

    async def build():
        async with create_agent(
            config, credentials=load_credentials(world.store, config), timeout_seconds=5, subagents=True
        ) as assistant:
            return assistant.instructions, [
                {'name': tool.name, 'description': tool.description} for tool in assistant.tools
            ]

    return asyncio.run(build())


def test_debug_shows_the_instructions_and_tools_the_runner_sends(world):
    ssh_key(world)
    sid = start(world)
    shown = debug(world, sid)
    instructions, tools = runner_view(world, sid)
    assert shown['instructions'] == instructions
    assert shown['tools'] == tools
    assert {tool['name'] for tool in tools} >= {'ssh_run', 'get_resource'}
    assert shown['model'] == {'id': world.model, 'name': 'GPT'}
    assert shown['prompt_source'] == 'library' and shown['prompt'] is None  # no library prompt was chosen
    assert shown['agent_instructions'] == '' and shown['skills'] == []
    assert shown['resources']['servers'] == ['web-1']
    assert shown['resources']['services'] == ['nginx']


def test_the_preview_shows_the_same_before_the_run_starts(world):
    ssh_key(world)
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body(world))
    assert preview.status_code == 200, preview.text
    shown = debug(world, start(world))
    assert preview.json()['instructions'] == shown['instructions']
    assert preview.json()['tools'] == shown['tools']
    assert preview.json()['config']['started_from']['type'] == 'server'


def test_a_library_prompt_and_its_version_and_a_wizard_prompt(world):
    ssh_key(world)
    prompt_id = assistant_prompt_id(world.project, 'server')
    with world.store.connect() as conn:
        content = conn.execute(
            """SELECT v.content FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v
            ON v.id=p.active_version_id WHERE p.id=%s""",
            (prompt_id,),
        ).fetchone()['content']
    shown = debug(world, start(world, prompt_id=str(prompt_id), system_prompt=content))
    assert shown['prompt'] == {'id': str(prompt_id), 'name': 'Server assistant', 'version': 1}
    assert shown['prompt_source'] == 'library'
    edited = debug(world, start(world, prompt_id=str(prompt_id), system_prompt=content + '\nAnd be brief.'))
    assert edited['prompt'] == {'id': str(prompt_id), 'name': 'Server assistant', 'version': None}
    wizard = debug(world, start(world, tags=['proposals'], tools=[*PLAIN, 'propose_document']))
    assert wizard['prompt_source'] == 'wizard' and wizard['prompt'] is None
    assert 'propose_document' in {tool['name'] for tool in wizard['tools']}


def test_secrets_never_show(world):
    key = ssh_key(world)
    model = keyed_model(world)
    secret_prompt = f'You answer. Key {MODEL_KEY}.\n{key}'
    change = {'model_id': model, 'system_prompt': secret_prompt}
    sid = start(world, **change)
    shown = debug(world, sid)
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body(world, **change)).json()
    for text in (str(shown), str(preview['instructions']), str(preview['tools'])):
        assert MODEL_KEY not in text
        assert key.strip().splitlines()[1] not in text
    assert '[redacted]' in shown['instructions'] and '[redacted]' in preview['instructions']
    instructions, _ = runner_view(world, sid)
    assert MODEL_KEY in instructions  # the runner does send it (it was in the prompt); Debug withholds it


def test_debug_of_an_unknown_run_is_not_found(world):
    assert world.client.get(f'/api/sessions/{uuid4()}/debug', headers=HEADERS).status_code == 404
