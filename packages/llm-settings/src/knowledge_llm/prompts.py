"""The agent's system prompt, read once at the start of each request."""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowledge_llm import ModelUnavailable, managed

DEFAULT_PROMPTS = {
    agent: (Path(__file__).parent / 'prompts' / f'{agent}.md').read_text(encoding='utf-8').strip()
    for agent in ('search', 'code', 'operations')
}


def active_prompt(agent: str) -> str:
    if not managed():
        return DEFAULT_PROMPTS[agent]
    try:
        with psycopg.connect('', row_factory=dict_row, connect_timeout=5) as conn:
            row = conn.execute('SELECT system_prompt FROM ka.agents WHERE id=%s', (agent,)).fetchone()
    except psycopg.Error:
        raise ModelUnavailable('Agent prompts are unavailable. Check the agent’s General tab.') from None
    if row is None or not row['system_prompt'].strip():
        raise ModelUnavailable('No system prompt. Check the agent’s General tab.')
    return row['system_prompt']
