"""Run an isolated commit update using LLM Settings inside the admin container.

Pass an output folder containing manifest.json and v1.md, then the saved model ID.
The manifest supplies the service ID, base/target commits and container repository path.
The caller prepares the test checkout; this script does not change source or publish docs.
"""
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from knowledge_admin.agents import load_credentials
from knowledge_admin import documentation_updates
from knowledge_admin.documentation_updates import propose_service_documentation_update
from knowledge_admin.repositories import RepositoryStore


async def main():
    output = Path(sys.argv[1])
    model_id = sys.argv[2]
    manifest = json.loads((output / 'manifest.json').read_text())
    store = RepositoryStore('', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL'])
    with store.connect() as conn:
        model = conn.execute('SELECT id,name,provider,model,api_format,base_url,aws_region,enabled,revision FROM ka.llm_models WHERE id=%s AND enabled', (model_id,)).fetchone()
        service = conn.execute('SELECT id,name,description FROM ka.services WHERE id=%s', (manifest['service_id'],)).fetchone()
    if model is None or service is None:
        raise ValueError('Saved model or service is unavailable')
    model = jsonable_encoder(model)
    model['api_key_ref'] = {'table': 'ka.llm_models', 'id': model_id, 'aad': f'llm:{model_id}:v1'}
    config = {'id': 'documentation-update', 'model': model, 'ssh': {'enabled': False}, 'mcp': {'enabled': False}}
    credentials = load_credentials(store, config)
    (output / 'model.json').write_text(json.dumps(model, indent=2))
    started = time.monotonic()
    execute = documentation_updates.agent
    proposals = 0

    async def record_proposal(*args, **kwargs):
        nonlocal proposals
        result = await execute(*args, **kwargs)
        proposals += 1
        (output / f'proposal-{proposals}.json').write_text(result.final_output.model_dump_json(indent=2))
        return result

    # Preserve model proposals for benchmark inspection, including failed validation.
    documentation_updates.agent = record_proposal

    async def activity(event):
        with (output / 'activity.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        print(event['title'], event['status'], flush=True)

    async def usage(value):
        with (output / 'usage.jsonl').open('a') as stream:
            stream.write(json.dumps(jsonable_encoder(value, exclude={'request_usage_entries'})) + '\n')

    try:
        result = await propose_service_documentation_update(
            service, Path(manifest['container_repository_path']), manifest['base_commit'],
            target_commit=manifest['target_commit'],
            documents={'personal-limits-api': (output / 'v1.md').read_text()},
            document_commits={'personal-limits-api': manifest['base_commit']},
            model=model, credentials=credentials, timeout_seconds=1200, max_turns=16,
            on_activity=activity, on_usage=usage,
        )
        (output / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
        (output / 'changes.md').write_text(result['change_report_markdown'])
        for update in result['updates']:
            (output / 'v2.md').write_text(update['markdown'])
        (output / 'status.json').write_text(json.dumps({'status': 'completed', 'elapsed_seconds': time.monotonic() - started}))
        print('Completed', json.dumps(result['token_usage']), flush=True)
    except BaseException as exc:
        (output / 'status.json').write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'elapsed_seconds': time.monotonic() - started}))
        raise
    finally:
        documentation_updates.agent = execute


if __name__ == '__main__':
    asyncio.run(main())
