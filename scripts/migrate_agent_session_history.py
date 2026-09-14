"""Import pre-migration SDK history files after migration 36, with the API stopped.

Uses PG* environment variables. Files remain available as a backup; runtime code
does not read them. Rejects stale history instead of overwriting newer turns.
"""

import argparse
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def import_history(conn, root: Path) -> int:
    root = root.resolve()
    imported = 0
    for session in conn.execute('SELECT id FROM ka.agent_sessions ORDER BY id').fetchall():
        session_id = session['id']
        source = root / str(session_id) / 'history.json'
        if not source.is_file():
            continue
        if not source.resolve().is_relative_to(root):
            raise ValueError(f'History is outside the session directory: {session_id}')
        if not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,27003)) AS locked',
                            (str(session_id),)).fetchone()['locked']:
            raise ValueError(f'Session is running: {session_id}. Stop the API before importing.')
        history = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(history,list) or any(not isinstance(item,dict) for item in history):
            raise ValueError(f'Invalid SDK history for session {session_id}')
        tasks = [row['task'] for row in conn.execute('''SELECT task FROM ka.operations_runs
            WHERE session_id=%s AND status='completed' ORDER BY created_at,id''', (session_id,))]
        if [item.get('content') for item in history if item.get('role') == 'user'] != tasks:
            raise ValueError(f'History does not match completed turns for session {session_id}; no changes committed.')
        conn.execute('UPDATE ka.agent_sessions SET history=%s WHERE id=%s', (Jsonb(history),session_id))
        imported += 1
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Former agent-sessions directory')
    args = parser.parse_args()
    with psycopg.connect('', row_factory=dict_row) as conn:
        count = import_history(conn,args.root)
    print(f'Imported complete SDK history for {count} sessions. Source files retained.')


if __name__ == '__main__':
    main()
