"""Incrementally index mapped code folders with CodeGraph, outside the source mount."""

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from knowledge_admin.code_paths import code_path, code_root

ENGINE_VERSION = '1.6.0'
PIPELINE_VERSION = 2
SKIP_DIRECTORIES = {'.git', '.ka-sync', '.codegraph', 'node_modules', 'vendor', 'dist', 'build',
    'target', '.venv', 'venv', '__pycache__', '.next', '.cache', 'coverage', 'Pods'}


class CodeProcessingError(Exception):
    pass


def index_directory(project_id: str) -> Path:
    root = os.environ.get('CODE_INDEX_ROOT')
    if not root or not Path(root).is_absolute():
        raise CodeProcessingError('Configure CODE_INDEX_ROOT as an absolute directory for processed code.')
    return Path(root) / str(UUID(str(project_id)))


def source_inventory(project: dict) -> dict:
    """Read the same source set for indexing and freshness checks, without running CodeGraph."""
    root = code_root()
    hashes, missing, skipped = {}, [], {'symlinks': 0, 'large_or_binary': 0}
    folders = []
    for value in sorted(set(project['folder_paths'])):
        path = Path(value)
        relative = str(path.relative_to(root)) if path.is_absolute() and path.is_relative_to(root) else value
        selected = code_path(root, relative)
        folders.append(relative)
        if not selected.is_dir():
            missing.append(relative)
            continue
        def fail_walk(error):
            raise error
        for parent, dirs, files in os.walk(selected, followlinks=False, onerror=fail_walk):
            skipped['symlinks'] += sum((Path(parent) / name).is_symlink() for name in dirs)
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRECTORIES
                and not name.startswith('.codegraph-') and not (Path(parent) / name).is_symlink())
            for name in sorted(files):
                # The index is evidence, never a credential store or a Git checkout.
                if name.startswith('.env') or name in ('id_rsa', 'id_ed25519', 'credentials', 'codegraph.json') or name.endswith(('.pem', '.key', '.p12', '.pfx')):
                    continue
                path = Path(parent) / name
                if path.is_symlink():
                    skipped['symlinks'] += 1
                    continue
                if not path.is_file():
                    continue
                key = str(path.relative_to(root))
                if key in hashes:
                    continue
                with path.open('rb') as file:
                    content = file.read(1_048_577)
                if len(content) > 1_048_576 or b'\0' in content:
                    skipped['large_or_binary'] += 1
                    continue
                digest = hashlib.sha256(content).hexdigest()
                hashes[key] = digest
    # Go's import resolver reads go.mod at the CodeGraph project root. Indexing
    # its parent silently loses package-qualified calls, even though parsing succeeds.
    common = os.path.commonpath(folders) if folders else '.'
    relative_root = common if str(Path(common) / 'go.mod') in hashes else '.'
    fingerprint = hashlib.sha256(json.dumps({'files': hashes, 'folders': folders, 'missing': missing,
        'relative_root': relative_root,
        'engine': ENGINE_VERSION, 'pipeline': PIPELINE_VERSION}, sort_keys=True).encode()).hexdigest()
    return {"files": hashes, "folders": folders, "missing_folders": missing, "excluded": skipped,
            "source_hash": fingerprint, "relative_root": relative_root}


def process_code(project: dict) -> dict:
    """Hash a filtered source snapshot, update changed files, and publish an immutable graph."""
    root = code_root()
    directory = index_directory(project['id'])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'processing.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CodeProcessingError('Another worker is generating this graph. Retry when it finishes.') from None
        source = directory / 'source'
        source.mkdir(exist_ok=True)
        manifest_path = directory / 'manifest.json'
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        inventory = source_inventory(project)
        hashes, folders, missing = inventory['files'], inventory['folders'], inventory['missing_folders']
        fingerprint = inventory['source_hash']
        relative_root = inventory['relative_root']
        analysis_root = source / relative_root
        snapshot = directory / fingerprint
        if previous.get('source_hash') == fingerprint and (snapshot / 'summary.json').is_file():
            return {**json.loads((snapshot / 'summary.json').read_text()), 'reused': True, 'name': project['name'],
                'graph_path': str(snapshot / 'graph.sqlite'), 'code_root': str(root / relative_root)}
        for key, digest in hashes.items():
            target = source / key
            if previous.get('files', {}).get(key) != digest or not target.is_file():
                content = (root / key).read_bytes()
                if hashlib.sha256(content).hexdigest() != digest:
                    raise CodeProcessingError('Source changed during graph generation. Generate the graph again.')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        for parent, dirs, files in os.walk(source):
            dirs[:] = [name for name in dirs if name != '.codegraph']
            for name in files:
                old = Path(parent) / name
                if str(old.relative_to(source)) not in hashes:
                    old.unlink()
        # Empty folders must also disappear so removed module roots cannot affect resolution.
        for parent, dirs, _ in os.walk(source, topdown=False):
            for name in dirs:
                folder = Path(parent) / name
                if '.codegraph' not in folder.relative_to(source).parts and not any(folder.iterdir()):
                    folder.rmdir()
        bridge = Path(os.environ.get('CODEGRAPH_BRIDGE', str(Path(__file__).resolve().parents[3] / 'code-graph/index.cjs')))
        output = directory / 'index-result.json'
        # Upstream's package is pinned. Disable telemetry, daemons and global Git config.
        env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': str(directory), 'CODEGRAPH_TELEMETRY': '0', 'DO_NOT_TRACK': '1',
            'CODEGRAPH_NO_DAEMON': '1', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'NODE_NO_WARNINGS': '1', 'CODEGRAPH_PARSE_WORKERS': '2', 'CODEGRAPH_RESOLVE_WORKERS': '2'}
        try:
            result = subprocess.run(['node', str(bridge), str(analysis_root), str(output)], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=False)
            if result.returncode != 0:
                raise CodeProcessingError('CodeGraph could not process this project. Check the worker installation and source files.')
            report = json.loads(output.read_text())
        except (OSError, subprocess.TimeoutExpired):
            raise CodeProcessingError('CodeGraph is unavailable or exceeded the 15-minute processing limit.') from None
        database = analysis_root / '.codegraph/codegraph.db'
        snapshot.mkdir(exist_ok=True)
        try:
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(snapshot / 'graph.sqlite') as saved:
                original.backup(saved)
                unresolved = saved.execute('SELECT count(*) FROM unresolved_refs').fetchone()[0]
                errors = saved.execute("SELECT path,errors FROM files WHERE errors IS NOT NULL AND errors NOT IN ('[]','')").fetchall()
        except sqlite3.Error:
            raise CodeProcessingError('CodeGraph could not save its graph snapshot. Check the index mount and regenerate the graph.') from None
        summary = {'id': str(project['id']), 'name': project['name'], 'engine': 'CodeGraph', 'engine_version': ENGINE_VERSION,
            'source_hash': fingerprint, 'folder_paths': [str(root / p) for p in folders], 'missing_folders': missing,
            'status': 'unavailable' if not hashes else 'unsupported' if report['stats']['fileCount'] == 0 else 'partial' if errors or missing or report['result'].get('success') is False else 'ready',
            'source_file_count': len(hashes), 'stats': report['stats'], 'unresolved_references': unresolved,
            'parse_error_files': [path for path, _ in errors], 'excluded': inventory['excluded'],
            'processed_at': datetime.now(timezone.utc).isoformat(), 'reused': False,
            'graph_path': str(snapshot / 'graph.sqlite'),
            'code_root': str(root / relative_root), 'relative_root': relative_root,
            'limitations': 'Static code relationships, not proof of runtime behavior. SQL schemas, state transitions and external effects require source inspection.'}
        (snapshot / 'summary.json').write_text(json.dumps(summary, indent=2))
        temporary = directory / 'manifest.tmp'
        temporary.write_text(json.dumps({**summary, 'files': hashes}))
        temporary.replace(manifest_path)
        return summary


def load_code_graph(project: dict, expected_hash: str | None = None) -> dict:
    """Require an explicitly generated graph that still describes the selected source."""
    directory = index_directory(project['id'])
    manifest = directory / 'manifest.json'
    if not manifest.is_file():
        raise CodeProcessingError('Generate and inspect the code graph in Phase 1 before generating documentation.')
    saved = json.loads(manifest.read_text())
    current = source_inventory(project)
    if saved['source_hash'] != current['source_hash'] or expected_hash != saved['source_hash']:
        raise CodeProcessingError('Source or graph selection changed. Generate and inspect the graph again before continuing.')
    graph_path = directory / saved['source_hash'] / 'graph.sqlite'
    if not graph_path.is_file():
        raise CodeProcessingError('The graph snapshot is unavailable. Generate the graph again.')
    return {key: value for key, value in {**saved, 'name': project['name'],
        'code_root': str(code_root() / saved.get('relative_root', '.')), 'graph_path': str(graph_path)}.items() if key != 'files'}
