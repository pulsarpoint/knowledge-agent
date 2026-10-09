"""Preserve existing IDs, version/review references, and complete SDK file history."""
import json
import runpy
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


def test_rename_preserves_history_and_import_refuses_stale_files(store,tmp_path):
    repo = Path(__file__).resolve().parents[3]
    up = (repo/'db/migrations/0036_agent_sessions.up.sql').read_text()
    down = (repo/'db/migrations/0036_agent_sessions.down.sql').read_text()
    session_id = uuid4()
    with store.connect() as conn:
        conn.execute((repo/'db/migrations/0084_remove_claude_code.down.sql').read_text(),prepare=False)
        conn.execute((repo/'db/migrations/0081_test_sessions.down.sql').read_text(),prepare=False)
        conn.execute((repo/'db/migrations/0080_clone_agents.down.sql').read_text(),prepare=False)  # restores agent_versions
        conn.execute(down,prepare=False)
        project = store.project(conn,'shop')
        actor = store.actor(conn,'migration-admin','migration@test')
        version = conn.execute("""INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('code',1,'Legacy','{}',%s) RETURNING id""",(actor,)).fetchone()['id']
        conn.execute("""INSERT INTO ka.agent_test_sessions(id,project_id,agent,actor_id,title,config,agent_version_id)
            VALUES (%s,%s,'code',%s,'Legacy session','{}',%s)""",(session_id,project,actor,version))
        conn.execute("""INSERT INTO ka.operations_runs(session_id,project_id,agent,actor_id,task,answer,status,context)
            VALUES (%s,%s,'code',%s,'Earlier task','Earlier answer','completed','{}')""",(session_id,project,actor))
        review = conn.execute("""INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
            VALUES (%s,%s,'Feedback','{}','Suggestion') RETURNING id""",(session_id,actor)).fetchone()['id']
    with store.connect() as conn:
        conn.execute(up,prepare=False)
        saved = conn.execute('SELECT * FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()
        assert saved['is_test'] is True and saved['agent_version_id'] == version
        assert saved['history'] == [{'role':'user','content':'Earlier task'},{'role':'assistant','content':'Earlier answer'}]
        assert conn.execute('SELECT session_id FROM ka.agent_session_reviews WHERE id=%s',(review,)).fetchone()['session_id'] == session_id
        assert conn.execute("SELECT to_regclass('ka.agent_test_sessions') AS old").fetchone()['old'] is None
        live = conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config)
            VALUES (%s,%s,'code',%s,'Live','{}') RETURNING is_test,history""",(uuid4(),project,actor)).fetchone()
        assert live == {'is_test':False,'history':[]}
    history = [
        {'role':'user','content':'Earlier task'},
        {'type':'function_call','call_id':'file-call','name':'read_file','arguments':'{}'},
        {'type':'function_call_output','call_id':'file-call','output':'Preserved tool evidence'},
        {'role':'assistant','content':'Earlier answer'},
    ]
    folder = tmp_path/str(session_id)
    folder.mkdir()
    (folder/'history.json').write_text(json.dumps(history),encoding='utf-8')
    import_history = runpy.run_path(str(repo/'scripts/migrate_agent_session_history.py'))['import_history']
    with store.connect() as conn:
        assert import_history(conn,tmp_path) == 1
        assert conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history'] == history
    assert (folder/'history.json').is_file()
    with pytest.raises(psycopg.errors.RaiseException,match='Cannot downgrade populated'):
        with store.connect() as conn:
            conn.execute(down,prepare=False)
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.operations_runs(session_id,project_id,agent,actor_id,task,answer,status,context)
            VALUES (%s,%s,'code',%s,'Newer task','Newer answer','completed','{}')""",(session_id,project,actor))
    with pytest.raises(ValueError,match='does not match completed turns'):
        with store.connect() as conn:
            import_history(conn,tmp_path)
