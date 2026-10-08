"""Run native commands in the separate shell worker, with the agent's selected paths."""

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities.shell import client
from knowledge_admin.tools import AgentContext


@function_tool
async def shell_run(ctx: RunContextWrapper[AgentContext], command: str,
                    timeout_seconds: int = 60) -> str:
    """Run Bash commands or Python scripts in an isolated Linux workspace.

    Available programs include bash, python/python3, rg, ast-grep, git, sed, awk,
    find and standard Linux utilities. Pipelines, Bash arrays and heredocs work.
    For Python use: python - <<'PY'\nimport json\nprint(json.dumps({'ok': True}))\nPY
    Use rg -n 'symbol' /work/data/code/project to locate code, then ast-grep or sed
    to inspect complete functions or focused ranges. Python's sqlite3 module can
    query configured graph databases; use sqlite3.connect('file:PATH?mode=ro', uri=True).
    Quote paths and use single quotes around ast-grep patterns containing $.

    The working directory is writable scratch space shared across shell calls
    within this run. Selected code, documents and graph paths retain the absolute
    paths in the agent resources and are read-only. Other projects, credentials
    and network access are unavailable. Use ssh_run for configured remote servers.
    Shell variables do not persist between calls; save intermediate work to files.
    Inspect exit_code, stderr and timed_out. Output over 32 KiB per stream is
    truncated: write large results to scratch files and inspect focused sections.

    Args:
        command: Bash command or multiline script, up to 20000 characters.
        timeout_seconds: Command timeout from 1 to 300 seconds.
    """
    return await client.shell_run(ctx.context, command, timeout_seconds)
