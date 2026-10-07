"""Native agents in Generate documents sessions: the session's working folders as the agent's code roots and a turn's
SDK lifecycle as the session's run events."""
import asyncio
import copy
import json
from typing import Any

from agents import RunHooks

from knowledge_admin.agent_activity import tool_failed
from knowledge_admin.knowledge_agents.session_worker import (MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT, PREVIEW_LINES, capped,
                                                             clean_text)


def code_roots(config: dict[str, Any], folders: list[dict[str, Any]]) -> dict[str, Any]:
    """The config a native session runs with: the agent's own config plus every working folder of the session that is
    not already one of its code project folders, as code roots grouped by code project. With such folders Code projects
    is on; the agent's own projects stay in only when its Code projects switch was on (a switched-off agent's saved
    selections stay out). Credentials stay references; nothing is decrypted here."""
    pinned = copy.deepcopy(config)
    own = pinned['code']['projects'] if pinned['code']['enabled'] else []
    known = {path for project in own for path in project['folder_paths']}
    added: dict[str, dict[str, Any]] = {}
    for folder in folders:
        if folder['path'] in known:
            continue
        known.add(folder['path'])
        project = added.setdefault(str(folder['code_project_id']), {
            'id': str(folder['code_project_id']), 'name': folder['name'], 'description': 'Chosen for this session.',
            'folder_paths': []})
        project['folder_paths'].append(folder['path'])
    if added:
        pinned['code'] = {**pinned['code'], 'enabled': True, 'all_projects': False, 'projects': [*own, *added.values()]}
    return pinned


def summary(name: str, arguments: Any) -> str:
    """One line for the session page, like Claude Code's Read/Grep/Glob/Bash summaries."""
    if not isinstance(arguments, dict):
        return ''

    def value(key: str) -> str:
        return str(arguments.get(key) or '')
    if name in ('read_file', 'list_files'):
        return value('path')
    if name == 'search_files':
        return f"{value('query')} in {value('path')}"
    if name == 'code_search':
        return f"{value('name')} in {value('path')}"
    if name == 'ast_grep':
        return f"{value('pattern') or value('rule')} in {value('path')}"
    if name == 'shell_run':
        return value('command')
    if name == 'ssh_run':
        return f"{value('server_id')}: {value('command')}"
    if name == 'search_documents':
        return value('query')
    if name == 'read_document':
        return f"{value('collection')}/{value('path')}"
    if name == 'code_graph':
        return f"{value('action')} {value('query') or value('node_id')}".strip()
    return ''


def _arguments(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


class SessionEvents(RunHooks):
    """A native turn's SDK lifecycle as the run events of a Claude Code turn: instructions (first turn), reasoning,
    message, tool_call with a summary, tool_result with a preview, and one usage event for the turn (finish). Every
    hook flushes, so the session page follows the turn live. The recorder redacts every event with `secrets`; tool
    output is also cleaned before it is capped, so no partial key survives the cut."""

    def __init__(self, recorder, *, secrets: list[str], first: bool):
        self.recorder, self.secrets, self.first = recorder, secrets, first
        self.texts: list[str] = []
        self.calls = 0
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}

    async def _flush(self) -> None:
        await asyncio.to_thread(self.recorder.flush)

    async def on_agent_start(self, context, agent) -> None:
        if not self.first:
            return
        self.first = False
        text = agent.instructions if isinstance(agent.instructions, str) else '(generated at run time)'
        self.recorder.add('instructions', {'agent': agent.name, 'text': text})
        await self._flush()

    async def on_llm_end(self, context, agent, response) -> None:
        for item in response.output:
            kind = getattr(item, 'type', None)
            if kind == 'reasoning':
                parts = [part.text for part in (getattr(item, 'summary', None) or [])]
                parts += [part.text for part in (getattr(item, 'content', None) or [])]
                if any(part.strip() for part in parts):
                    self.recorder.add('reasoning', {'text': '\n'.join(parts)})
            elif kind == 'message':
                text = ''.join(getattr(part, 'text', '') for part in item.content)
                if text.strip():
                    self.texts.append(text)
                    self.recorder.add('message', {'text': text})
            elif kind == 'function_call':
                arguments = _arguments(item.arguments)
                self.recorder.add('tool_call', {'name': item.name, 'call_id': item.call_id, 'arguments': arguments,
                                                'summary': summary(item.name, arguments)})
        usage = response.usage
        details = getattr(usage, 'input_tokens_details', None)
        self.usage['input_tokens'] += int(usage.input_tokens or 0)
        self.usage['output_tokens'] += int(usage.output_tokens or 0)
        self.usage['cached_tokens'] += int(getattr(details, 'cached_tokens', 0) or 0)
        self.usage['total_tokens'] += int(usage.total_tokens or 0)
        self.calls += 1
        await self._flush()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        output = clean_text(str(result), self.secrets)
        self.recorder.add('tool_result', {
            'name': tool.name, 'call_id': getattr(context, 'tool_call_id', None) or '',
            'output': capped(output, MAX_TOOL_OUTPUT),
            'preview': capped('\n'.join(output.splitlines()[:PREVIEW_LINES]), MAX_PREVIEW_CHARS),
            'is_error': tool_failed(output)})
        await self._flush()

    def finish(self) -> None:
        """The turn's one usage event (sums of its model calls); none when no model call finished."""
        if self.calls:
            self.recorder.add('usage', dict(self.usage))

    def draft(self, final_output: Any) -> str:
        """The final answer, or the last message when the answer is empty."""
        final = '' if final_output is None else str(final_output)
        return final if final.strip() else (self.texts[-1] if self.texts else '')
