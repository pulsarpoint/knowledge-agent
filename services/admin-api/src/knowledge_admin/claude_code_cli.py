"""Start the bundled Claude Code CLI as the unprivileged `claude` user.

The claude-code-worker runs as root so that only it can read its environment (database password, encryption key).
ClaudeAgentOptions.cli_path points to this script, which drops to `claude` (groups, gid, uid) before running the CLI,
so Bash in the agent cannot read the worker's process environment. The SDK starts this script with the worker's whole
environment plus the session's; only an allowlist reaches the CLI."""
import importlib.util
import os
import pwd
import sys
from pathlib import Path

USER = 'claude'
ALLOWED_ENV = frozenset({
    'PATH', 'HOME', 'LANG', 'TZ', 'PWD', 'TMPDIR',
    'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY', 'DISABLE_ERROR_REPORTING',
    'CLAUDE_CODE_USE_BEDROCK', 'AWS_REGION', 'AWS_BEARER_TOKEN_BEDROCK', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL',
    'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL'})
ALLOWED_PREFIXES = ('LC_', 'CLAUDE_CODE_', 'CLAUDE_AGENT_SDK_')


def bundled_cli() -> str:
    """The CLI shipped inside the claude-agent-sdk wheel, found without importing the package."""
    spec = importlib.util.find_spec('claude_agent_sdk')
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit('claude-agent-sdk is not installed')
    return str(Path(next(iter(spec.submodule_search_locations))) / '_bundled' / 'claude')


def cli_environment(environ) -> dict[str, str]:
    """Only what the CLI needs: locale and paths, the session's BASE_ENV and credential variables, CLAUDE_CODE_*."""
    return {name: value for name, value in environ.items() if name in ALLOWED_ENV or name.startswith(ALLOWED_PREFIXES)}


def main(argv: list[str] | None = None, *, os_module=os) -> None:
    user = pwd.getpwnam(USER)
    if user.pw_uid == 0:
        raise SystemExit(f'The {USER} user must not be root')
    if os_module.getuid() == 0:
        os_module.setgroups([])
        os_module.setgid(user.pw_gid)
        os_module.setuid(user.pw_uid)
    if os_module.getuid() != user.pw_uid:
        raise SystemExit(f'Claude Code must run as {USER}')
    target = bundled_cli()
    os_module.execve(target, [target, *(sys.argv[1:] if argv is None else argv)], cli_environment(os_module.environ))
