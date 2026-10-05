"""Internal Bash execution worker. Receives paths and commands, never agent credentials."""

import asyncio
import os
import signal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class ShellRequest(BaseModel):
    command: str = Field(min_length=1, max_length=20000)
    workspace: str
    read_paths: list[str] = Field(max_length=100)
    timeout_seconds: int = Field(default=60, ge=1, le=300)


@app.get('/health')
def health():
    return {'status': 'ok'}


async def execute(value: ShellRequest) -> dict:
    """Mount only the selected resources into a fresh Linux namespace for this command."""
    workspace = Path(value.workspace)
    if (not workspace.is_absolute() or workspace.resolve() != workspace or not workspace.is_dir()
            or workspace.parent != Path('/work/agent-workspaces')):
        raise HTTPException(422, 'Choose an existing agent workspace.')
    if not value.command.strip() or '\0' in value.command:
        raise HTTPException(422, 'Provide a shell command without null bytes.')
    roots = [Path(os.environ.get(name, default)) for name, default in (
        ('CODE_ROOT', '/work/code'), ('RAG_SOURCE_ROOT', '/work/rag-data'),
        ('CODE_INDEX_ROOT', '/work/code-index'), ('DOCUMENT_OUTPUT_ROOT', '/work/generated-documents'),
        ('BOOTSTRAP_DOCS_ROOT', '/work/docs'))]
    selected = []
    for name in value.read_paths:
        path = Path(name)
        if (not path.is_absolute() or path.resolve() != path or not (path.is_dir() or path.is_file())
                or not any(path.is_relative_to(root) for root in roots)):
            raise HTTPException(422, 'A selected source path is unavailable or outside the mounted resource folders.')
        if path not in selected:
            selected.append(path)
    args = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--clearenv', '--setenv', 'PATH', '/usr/local/bin:/usr/bin:/bin',
            '--setenv', 'HOME', str(workspace), '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'TMPDIR', '/tmp', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    for name in ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc/ld.so.cache', '/etc/passwd', '/etc/group'):
        if Path(name).exists():
            args.extend(['--ro-bind', name, name])
    # Docker masks procfs mount sources. An empty /proc also hides worker/sibling processes.
    args.extend(['--dir', '/proc', '--dev', '/dev', '--tmpfs', '/tmp'])
    for path in sorted(selected, key=lambda p: len(p.parts)):
        args.extend(['--ro-bind', str(path), str(path)])
    args.extend(['--bind', str(workspace), str(workspace), '--chdir', str(workspace),
                 '--', '/bin/bash', '--noprofile', '--norc', '-c', value.command])
    process = await asyncio.create_subprocess_exec(*args, env={'PATH': '/usr/local/bin:/usr/bin:/bin'},
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = {'stdout': False, 'stderr': False}

    async def capture(name, stream):
        while chunk := await stream.read(4096):
            available = 32768 - len(output[name])
            output[name].extend(chunk[:available])
            truncated[name] = truncated[name] or len(chunk) > available

    readers = [asyncio.create_task(capture('stdout', process.stdout)),
               asyncio.create_task(capture('stderr', process.stderr))]
    timed_out = False
    try:
        async with asyncio.timeout(value.timeout_seconds):
            await process.wait()
            await asyncio.gather(*readers)
    except TimeoutError:
        timed_out = True
    finally:
        # Killing bwrap also tears down its PID namespace and background children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    return {'exit_code': process.returncode, 'timed_out': timed_out,
            **{name: data.decode('utf-8', errors='replace') for name, data in output.items()},
            'truncated': truncated}


@app.post('/run')
async def run_command(value: ShellRequest, request: Request):
    execution = asyncio.create_task(execute(value))
    try:
        while not execution.done():
            if await request.is_disconnected():
                raise HTTPException(499, 'The agent disconnected.')
            await asyncio.wait({execution}, timeout=0.1)
        return await execution
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
