"""Pull repositories into complete, independently usable local checkouts."""

import base64
import fcntl
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from knowledge_admin.events import processor as event_processor, scheduler, templates as event_templates
from knowledge_admin.repositories import RepositoryStore, git_url
from knowledge_admin.code_projects.paths import code_path
from knowledge_admin.code_projects.processing import process_code, CodeProcessingError


class PullError(Exception):
    pass


def pull_checkout(code_root: Path, repo: dict, job_id, username: str | None, token: str | None) -> tuple[str, str]:
    """Never execute source, hooks, LFS filters, or submodules from a fetched repo."""
    remote = git_url(repo["remote_url"])
    code_root.mkdir(parents=True, exist_ok=True)
    code_root = code_root.resolve()
    managed = repo.get('clone_path') is not None
    relative = Path('.ka-sync') / str(repo['id']) if managed else Path(str(repo["project_id"])) / str(repo["id"])
    try:
        directory = code_path(code_root, str(relative))
        target = code_path(code_root, repo['clone_path']) if managed else None
    except HTTPException as exc:
        raise PullError(str(exc.detail)) from None
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / "cache.git"
    staging = directory / f".pull-{job_id}"
    checkout = directory / "checkouts" / str(job_id)
    deadline = time.monotonic() + 600
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ASKPASS": str(Path(__file__).with_name("git_askpass.py")),
        "KA_GITHUB_TOKEN": token or "", "LC_ALL": "C",
        "KA_GIT_USERNAME": username or "",
    }

    def git(*args: str, local_clone: bool = False) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PullError("Pull exceeded the 10-minute limit")
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "credential.helper=",
                   "-c", "http.followRedirects=false", "-c", "protocol.allow=never",
                   "-c", "protocol.https.allow=always"]
        if local_clone:
            command += ["-c", "protocol.file.allow=always"]
        try:
            result = subprocess.run([*command, *args], env=env, check=False,
                                    capture_output=True, text=True, timeout=min(300, remaining))
        except subprocess.TimeoutExpired:
            raise PullError("Git timed out. Check connectivity and try again") from None
        if result.returncode != 0:
            # Remote output can contain credentials or arbitrary text; never persist or return it.
            raise PullError("Git pull failed. Check the URL, branch, token permissions, and organization approval")
        return result.stdout.strip()

    with (directory / ".sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PullError("Another worker is still using this repository. Try again after it finishes") from None
        try:
            if target is not None and target.exists():
                if not target.is_dir() or repo['current_commit'] is None or not (target / '.git').is_dir() or (target / '.git').is_symlink():
                    raise PullError('The clone folder is occupied by other files. Choose an unused folder.')
                if git('-C', str(target), 'rev-parse', 'HEAD') != repo['current_commit'] or git('-C', str(target), 'status', '--porcelain', '--untracked-files=normal', '--ignored'):
                    raise PullError('The clone has local changes. Commit or remove those changes before pulling.')
                if git('-C', str(target), 'config', '--get', 'remote.origin.url') != remote:
                    raise PullError('The clone remote changed. Restore its configured origin before pulling.')
                git('-C', str(target), 'fetch', '--no-tags', '--depth=1', 'origin', repo['requested_ref'] or 'HEAD')
                commit = git('-C', str(target), 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
                git('-C', str(target), 'checkout', '--detach', '--no-overwrite-ignore', commit)
                return commit, repo['clone_path']
            git("init", "--bare", str(cache))
            git(f"--git-dir={cache}", "config", "remote.origin.url", remote)
            git(f"--git-dir={cache}", "fetch", "--no-tags", "--depth=1", "origin", repo["requested_ref"] or "HEAD")
            commit = git(f"--git-dir={cache}", "rev-parse", "--verify", "FETCH_HEAD^{commit}")
            git(f"--git-dir={cache}", "update-ref", "refs/heads/source", commit)
            git(f"--git-dir={cache}", "symbolic-ref", "HEAD", "refs/heads/source")
            # Copy objects rather than symlinking/alternates: the checkout's .git also works on the host.
            git("clone", "--no-hardlinks", "--no-checkout", "--", str(cache), str(staging), local_clone=True)
            git("-C", str(staging), "remote", "set-url", "origin", remote)
            git("-C", str(staging), "checkout", "--detach", commit)
            if target is not None:
                if target.exists():
                    raise PullError('The clone folder was created while cloning. Choose an unused folder.')
                checkout = target
            checkout.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(checkout)
            return commit, str(checkout.relative_to(code_root))
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def run_once(store: RepositoryStore, code_root: Path) -> bool:
    lease = uuid4()
    with store.connect() as conn:
        conn.execute("""UPDATE ka.jobs SET status='failed', error='Worker stopped before completing this job. Run the action again to retry.',
            finished_at=clock_timestamp(), worker_id=NULL, lease_token=NULL, lease_expires_at=NULL
            WHERE kind IN ('repository_sync','code_index') AND status='running' AND lease_expires_at < clock_timestamp()""")
        due = conn.execute('''SELECT r.id,r.project_id,r.sync_interval_minutes FROM ka.repositories r
            WHERE r.archived_at IS NULL AND r.remote_url IS NOT NULL AND r.sync_interval_minutes IS NOT NULL
                AND r.next_sync_at<=now() AND NOT EXISTS (SELECT 1 FROM ka.jobs j WHERE j.repository_id=r.id
                    AND j.status IN ('queued','running','waiting_input'))
            ORDER BY r.next_sync_at,r.id FOR UPDATE OF r SKIP LOCKED LIMIT 20''').fetchall()
        for source in due:
            job_id = uuid4()
            conn.execute('''INSERT INTO ka.jobs(id,project_id,kind,repository_id,idempotency_key)
                VALUES (%s,%s,'repository_sync',%s,%s) ON CONFLICT DO NOTHING''',
                (job_id,source['project_id'],source['id'],str(job_id)))
            conn.execute('UPDATE ka.repositories SET next_sync_at=now()+make_interval(mins=>%s) WHERE id=%s',
                (source['sync_interval_minutes'],source['id']))
        job = conn.execute("""SELECT * FROM ka.jobs WHERE kind IN ('repository_sync','code_index') AND status='queued'
            ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if job is None:
            return False
        conn.execute("""UPDATE ka.jobs SET status='running', started_at=clock_timestamp(), attempt=attempt+1,
            worker_id=%s, lease_token=%s, lease_expires_at=clock_timestamp()+interval '20 minutes'
            WHERE id=%s""", (socket.gethostname(), lease, job["id"]))
        repo = conn.execute("""SELECT r.*, c.ciphertext, c.key_version FROM ka.repositories r
            LEFT JOIN ka.credentials c ON c.id=r.credential_id WHERE r.id=%s""", (job["repository_id"],)).fetchone()
        repo['folder_paths'] = [r['path'] for r in conn.execute('SELECT path FROM ka.code_project_folders WHERE code_project_id=%s ORDER BY path', (repo['id'],))]
    error = None
    result = {}
    try:
        if job['kind'] == 'code_index':
            result = process_code(repo)
        else:
            username, token = None, None
            if repo["credential_id"] is not None:
                username, token = store.decrypt_git_credentials(repo)
            commit, checkout_path = pull_checkout(code_root, repo, job["id"], username, token)
            result = {"commit": commit, "checkout_path": checkout_path}
    except InvalidTag:
        error = "The stored Git authentication cannot be decrypted. Restore the encryption key or save the username and token again"
    except (PullError, CodeProcessingError) as exc:
        error = str(exc)
    except OSError:
        error = "Unable to write the local checkout. Check the code folder mount, free space, and permissions"
    except ValueError:
        error = "Invalid repository settings or stored Git authentication. Update the repository and retry"
    with store.connect() as conn:
        active = conn.execute("""SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s
            AND lease_expires_at > clock_timestamp() FOR UPDATE""", (job["id"], lease)).fetchone()
        if active is None:
            return True  # Stale workers never publish checkout metadata.
        if error is None and job['kind'] == 'repository_sync':
            conn.execute("""UPDATE ka.repositories SET current_commit=%s, checkout_path=%s, synced_at=clock_timestamp()
                WHERE id=%s""", (result["commit"], result["checkout_path"], repo["id"]))
            if repo['clone_path'] is None:
                conn.execute('DELETE FROM ka.code_project_folders WHERE code_project_id=%s', (repo['id'],))
            conn.execute('''INSERT INTO ka.code_project_folders(code_project_id,project_id,path)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''', (repo['id'],repo['project_id'],result['checkout_path']))
        conn.execute("""UPDATE ka.jobs SET status=%s, result=%s, error=%s, finished_at=clock_timestamp(),
            worker_id=NULL, lease_token=NULL, lease_expires_at=NULL WHERE id=%s""",
            ("succeeded" if error is None else "failed", Jsonb(result), error, job["id"]))
        conn.execute("""INSERT INTO ka.job_events(job_id, event_type, message)
            VALUES (%s,%s,%s)""", (job["id"], "succeeded" if error is None else "failed", error or ('Code processed' if job['kind'] == 'code_index' else 'Checkout published')))
    print(f"Repository job {job['id']}: {'succeeded' if error is None else 'failed'}", flush=True)
    return True


def main() -> None:
    key = base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"])
    store = RepositoryStore("", key, os.environ["AUTH_ISSUER_URL"])
    code_root = Path(os.environ["CODE_ROOT"]).resolve()
    code_root.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    # Schedules (e.g. the Jira auto pull) run beside the repository jobs; a running target finishes before exit.
    schedules = threading.Thread(target=scheduler.loop, args=(store, stopped), name="scheduler")
    schedules.start()
    # Pending events go through the event handlers every 2 s (event_processor.py).
    events = threading.Thread(target=event_processor.loop, args=(store, stopped), name="event-processor")
    events.start()
    try:
        while not stopped.is_set():
            try:
                worked = run_once(store, code_root)
            except psycopg.Error:
                print("Repository worker database unavailable; retrying", flush=True)
                worked = False
            if not worked:
                stopped.wait(2)
    finally:
        stopped.set()
        schedules.join()
        events.join()
        event_templates.shutdown()
