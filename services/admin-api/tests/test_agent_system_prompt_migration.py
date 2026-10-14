"""0079: an agent's prompt is one text field filled from its active prompt version; prompt rows are no longer
required. Down gives every agent its prompt back as the active prompt version."""

from pathlib import Path
from uuid import uuid4

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0079_agent_system_prompt.up.sql', '0079_agent_system_prompt.down.sql'
# Later migrations roll back first, newest first (0080 restores the prompt tables empty).
LATER = sorted((path.name for path in MIGRATIONS.glob('*.down.sql') if path.name[:4] > '0079'), reverse=True)
PLACEHOLDER = "Follow the administrator's instructions."


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def active(conn, agent_id: str):
    return conn.execute(
        """SELECT v.version,v.title,v.content,p.next_version FROM ka.agent_prompts p
        JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE p.agent=%s""",
        (agent_id,),
    ).fetchone()


def has_fk(conn) -> bool:
    return conn.execute("SELECT 1 FROM pg_constraint WHERE conname='agent_system_prompt_fk'").fetchone() is not None


def has_column(conn) -> bool:
    return (
        conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='system_prompt'""").fetchone()
        is not None
    )


def test_up_moves_each_active_prompt_into_its_agent(store):
    with store.connect() as conn:
        for name in [*LATER, DOWN]:
            run(conn, name)
        conn.commit()
        assert has_fk(conn) and not has_column(conn)
        version = uuid4()
        conn.execute(
            """INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at)
            SELECT %s,'search',next_version,'Evidence','Answer with evidence.',now() FROM ka.agent_prompts
            WHERE agent='search'""",
            (version,),
        )
        conn.execute(
            "UPDATE ka.agent_prompts SET active_version_id=%s,next_version=next_version+1 WHERE agent='search'",
            (version,),
        )
        conn.execute("""INSERT INTO ka.agent_prompt_versions(agent,version,title,content)
            SELECT 'code',next_version,'Draft','Never runs.' FROM ka.agent_prompts WHERE agent='code'""")  # a draft
        conn.commit()
        expected = {
            row['id']: row['content']
            for row in conn.execute("""SELECT a.id,v.content FROM ka.agents a
            JOIN ka.agent_prompts p ON p.agent=a.id JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id""")
        }
        run(conn, UP)
        conn.commit()
        assert {
            row['id']: row['system_prompt'] for row in conn.execute('SELECT id,system_prompt FROM ka.agents')
        } == expected
        assert expected['search'] == 'Answer with evidence.' and expected['code'] != 'Never runs.'
        assert not has_fk(conn)
        conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt) VALUES ('fresh','Fresh','Be brief.')"
        )  # no prompt rows
        conn.commit()


def test_down_gives_every_agent_its_prompt_as_the_active_version(store):
    with store.connect() as conn:
        for name in LATER:
            run(conn, name)
        conn.commit()
        conn.execute(
            "INSERT INTO ka.agents(id,name,system_prompt) VALUES ('fresh','Fresh','Be brief.'),('blank','Blank','')"
        )
        conn.execute("UPDATE ka.agents SET system_prompt='Edited after 0079.' WHERE id='search'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert has_fk(conn) and not has_column(conn)
        assert active(conn, 'fresh') == {
            'version': 1,
            'title': 'System prompt',
            'content': 'Be brief.',
            'next_version': 2,
        }
        assert active(conn, 'blank')['content'] == PLACEHOLDER
        assert active(conn, 'search')['content'] == 'Edited after 0079.'
        run(conn, UP)
        conn.commit()
        assert (
            conn.execute("SELECT system_prompt FROM ka.agents WHERE id='search'").fetchone()['system_prompt']
            == 'Edited after 0079.'
        )
