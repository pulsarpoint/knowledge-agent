"""Start the bundled Claude Code CLI as the unprivileged user of one worker slot.

The claude-code-worker runs as root so that only it can read its environment (database password, encryption key).
ClaudeAgentOptions.cli_path points to this script, which drops to the slot's user (groups, gid, uid) before running the
CLI, so Bash in the agent cannot read the worker's process environment. Each running session has its own slot, and so
its own uid (claude1..claude8): Bash in one session cannot read another session's CLI environment (/proc/<pid>/environ)
or files. The worker passes the slot in KNOWLEDGE_CLAUDE_SLOT, which this script removes.

HOME is the session's own folder, /home/claude/sessions/<session id>, so its transcript stays with the session when a
later turn runs in another slot (resume): before dropping privileges, this script gives that folder to the slot's user.
The SDK starts this script with the worker's whole environment plus the session's; only an allowlist reaches the CLI."""
import errno
import importlib.util
import os
import pwd
import re
import signal
import stat
import sys
from pathlib import Path

SLOT_ENV = 'KNOWLEDGE_CLAUDE_SLOT'
MAX_SLOTS = 8  # claude1..claude8 exist in the image (deploy/Dockerfile.admin-api)
HOME_ROOT = '/home/claude'
SESSION_HOMES = f'{HOME_ROOT}/sessions'
SESSION_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
ALLOWED_ENV = frozenset({
    'PATH', 'HOME', 'LANG', 'TZ', 'PWD', 'TMPDIR', 'SHELL', 'USER', 'LOGNAME', 'TERM',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
    'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
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


def slot_user(value: str | None) -> str:
    if not value or not value.isdigit() or not 1 <= int(value) <= MAX_SLOTS:
        raise SystemExit(f'Claude Code needs a worker slot from 1 to {MAX_SLOTS} in {SLOT_ENV}')
    return f'claude{int(value)}'


def session_home(value: str | None) -> str:
    parent, _, name = (value or '').rpartition('/')
    if parent != SESSION_HOMES or not SESSION_ID.fullmatch(name):
        raise SystemExit(f'Claude Code needs HOME={SESSION_HOMES}/<session id>')
    return value


def own_tree(path: str, uid: int, gid: int, os_module=os) -> None:
    """Give a session folder to a slot user with CAP_CHOWN alone (root has no CAP_DAC_* here): each directory becomes
    root's first, so root may list it and its previous owner can no longer change it, then its entries are handed over,
    then the directory itself. Symbolic links are never followed; a file with other hard links is left alone."""
    info = os_module.lstat(path)
    if stat.S_ISDIR(info.st_mode):
        os_module.lchown(path, 0, 0)
        os_module.chmod(path, 0o700)
        for name in os_module.listdir(path):
            own_tree(f'{path}/{name}', uid, gid, os_module)
        os_module.lchown(path, uid, gid)
    elif not (stat.S_ISREG(info.st_mode) and info.st_nlink > 1):
        os_module.lchown(path, uid, gid)


def prepare_home(home: str, uid: int, gid: int, os_module=os) -> None:
    """The session's HOME with a tmp folder, owned by the slot user and private to it (0700)."""
    try:
        os_module.mkdir(home, 0o700)
    except FileExistsError:
        pass
    if not stat.S_ISDIR(os_module.lstat(home).st_mode):
        raise SystemExit(f'{home} is not a folder')
    os_module.lchown(home, 0, 0)  # root may now create tmp in it; the previous slot user can no longer change it
    os_module.chmod(home, 0o700)
    try:
        os_module.mkdir(f'{home}/tmp', 0o700)
    except FileExistsError:
        pass
    own_tree(home, uid, gid, os_module)


def main(argv: list[str] | None = None, *, os_module=os) -> None:
    environ = dict(os_module.environ)
    name = slot_user(environ.pop(SLOT_ENV, None))
    home = session_home(environ.get('HOME'))
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        raise SystemExit(f'The {name} user does not exist in this image; Claude Code cannot run.') from None
    if user.pw_uid == 0:
        raise SystemExit(f'The {name} user must not be root')
    if os_module.getuid() == 0:
        prepare_home(home, user.pw_uid, user.pw_gid, os_module)
        os_module.setgroups([])
        os_module.setgid(user.pw_gid)
        os_module.setuid(user.pw_uid)
    if os_module.getuid() != user.pw_uid:
        raise SystemExit(f'Claude Code must run as {name}')
    if os_module.getgid() != user.pw_gid:
        raise SystemExit(f'Claude Code must run with the {name} group')
    # Processes an earlier session left in this slot (a Bash job in the background) must not see this session.
    try:
        os_module.kill(-1, signal.SIGKILL)  # as the slot user: every process of this uid except this one
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise
    environ['TMPDIR'] = f'{home}/tmp'
    target = bundled_cli()
    os_module.execve(target, [target, *(sys.argv[1:] if argv is None else argv)], cli_environment(environ))
