"""Claude Code worker: runs interactive documentation sessions with the Claude Agent SDK, one client per session.

The model key is decrypted here and passed only in the CLI process environment. Nothing imports claude_agent_sdk at
module import time; messages are recognised by class name, so tests drive the worker with same-named fakes."""
import logging
import os
from collections.abc import Iterable
from typing import Any

from knowledge_llm import RuntimeModel, claude_model

from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import ALLOWED_TOOLS, DISALLOWED_TOOLS

logger = logging.getLogger(__name__)

PREVIEW_LINES = 20
BASE_ENV = {'HOME': '/home/claude', 'TMPDIR': '/home/claude/tmp', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1'}
HINTS = (
    (('inference profile', 'on-demand throughput'),
     'Bedrock needs an inference profile ID for this model, such as eu.anthropic.claude-sonnet-4-5-20250929-v1:0. '
     'Change the model ID in LLM Settings.'),
    (('403', '401', 'accessdenied', 'authentication'), 'Check the key and model access of this connection in LLM Settings.'),
)
DEFAULT_HINT = 'Check the model connection in LLM Settings, then send a message to retry.'


def credential_env(model: RuntimeModel) -> dict[str, str]:
    """The CLI environment variables that carry this connection's key; nothing else ever receives the key."""
    if not claude_model(model.provider, model.api_format, model.model):
        raise ValueError('The session model is not a Claude connection. Start a new session with a Claude model.')
    if not model.api_key:
        raise ValueError('The model connection has no key. Save its key in LLM Settings.')
    if model.provider == 'bedrock':
        return {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': model.aws_region or '',
                'AWS_BEARER_TOKEN_BEDROCK': model.api_key, 'ANTHROPIC_MODEL': model.model}
    env = {'ANTHROPIC_API_KEY': model.api_key, 'ANTHROPIC_MODEL': model.model}
    if model.base_url:
        base = model.base_url.rstrip('/')
        env['ANTHROPIC_BASE_URL'] = base[:-3] if base.endswith('/v1') else base
    return env


def build_options(session: dict[str, Any], model: RuntimeModel) -> dict[str, Any]:
    """Keyword arguments for ClaudeAgentOptions: fixed read-only tools plus Bash, the chosen folders, no limits."""
    folders = [folder['path'] for folder in session['context_snapshot']['folders']]
    options = {
        'system_prompt': {'type': 'preset', 'preset': 'claude_code', 'append': session['prompt_snapshot']['system_prompt']},
        'tools': list(ALLOWED_TOOLS), 'allowed_tools': list(ALLOWED_TOOLS), 'disallowed_tools': list(DISALLOWED_TOOLS),
        'permission_mode': 'dontAsk', 'setting_sources': [], 'skills': [], 'mcp_servers': {}, 'strict_mcp_config': True,
        'cwd': folders[0], 'add_dirs': folders, 'model': model.model, 'env': {**BASE_ENV, **credential_env(model)},
        'resume': session['claude_session_id'],
    }
    if os.environ.get('CLAUDE_CODE_CLI'):
        options['cli_path'] = os.environ['CLAUDE_CODE_CLI']
    return options


def safe_error(text: str, secrets: Iterable[str]) -> str:
    """A provider or CLI error for people: redacted, one line, at most 500 characters, with a hint."""
    reason = ' '.join(redact(text or '', [secret for secret in secrets if secret]).split())[:500]
    reason = reason or 'Claude Code stopped with an error.'
    lowered = reason.lower()
    hint = next((hint for needles, hint in HINTS if any(needle in lowered for needle in needles)), DEFAULT_HINT)
    return f'{reason} {hint}'


def result_text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text')


def tool_summary(name: str, arguments: dict[str, Any]) -> str:
    if name == 'Read':
        return str(arguments.get('file_path', ''))
    if name in ('Grep', 'Glob'):
        pattern, path = str(arguments.get('pattern', '')), arguments.get('path')
        return f'{pattern} in {path}' if path else pattern
    if name == 'Bash':
        return str(arguments.get('command', ''))
    return ''


def usage_payload(result: Any) -> dict[str, Any]:
    usage = result.usage or {}
    cached = int(usage.get('cache_read_input_tokens') or 0)
    prompt = int(usage.get('input_tokens') or 0) + cached + int(usage.get('cache_creation_input_tokens') or 0)
    output = int(usage.get('output_tokens') or 0)
    return {'input_tokens': prompt, 'output_tokens': output, 'cached_tokens': cached, 'total_tokens': prompt + output,
            'cost_usd': float(result.total_cost_usd or 0)}


def result_error(result: Any) -> str:
    if result is None:
        return 'Claude Code ended the turn without a result.'
    return ' '.join(part for part in [result.result or '', *(getattr(result, 'errors', None) or [])] if part)


def _scrub(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: _scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, secrets) for item in value]
    return value


class _Redacting:
    """Removes the connection's key from every event: Bash runs in the CLI environment, so `env` could print it."""

    def __init__(self, recorder, secrets: list[str]):
        self.recorder, self.secrets = recorder, secrets

    def add(self, kind: str, payload: dict) -> None:
        self.recorder.add(kind, _scrub(payload, self.secrets) if self.secrets else payload)


class Turn:
    """One turn's SDK messages as run record events, plus its draft, usage and CLI session id."""

    def __init__(self, recorder, secrets: Iterable[str] = ()):
        self.recorder = _Redacting(recorder, [secret for secret in secrets if secret])
        self.tools: dict[str, str] = {}
        self.texts: list[str] = []
        self.result = None
        self.usage: dict[str, Any] | None = None
        self.session_id: str | None = None

    def add(self, message: Any) -> None:
        kind = type(message).__name__
        if kind == 'SystemMessage' and message.subtype == 'init':
            self.session_id = message.data.get('session_id') or self.session_id
        elif kind == 'AssistantMessage':
            for block in message.content:
                self._block(block)
            if getattr(message, 'error', None):
                self.recorder.add('error', {'message': f'The model reported an error: {message.error}'})
        elif kind == 'UserMessage' and isinstance(message.content, list):
            for block in message.content:
                if type(block).__name__ == 'ToolResultBlock':
                    output = result_text(block.content)
                    self.recorder.add('tool_result', {
                        'name': self.tools.get(block.tool_use_id, ''), 'call_id': block.tool_use_id, 'output': output,
                        'preview': '\n'.join(output.splitlines()[:PREVIEW_LINES]), 'is_error': bool(block.is_error)})
        elif kind == 'ResultMessage':
            self.result = message
            self.session_id = message.session_id or self.session_id
            self.usage = usage_payload(message)
            self.recorder.add('usage', self.usage)

    def _block(self, block: Any) -> None:
        name = type(block).__name__
        if name == 'TextBlock' and block.text.strip():
            self.texts.append(block.text)
            self.recorder.add('message', {'text': block.text})
        elif name == 'ThinkingBlock' and block.thinking.strip():
            self.recorder.add('reasoning', {'text': block.thinking})
        elif name == 'ToolUseBlock':
            self.tools[block.id] = block.name
            self.recorder.add('tool_call', {'name': block.name, 'call_id': block.id, 'arguments': block.input,
                                            'summary': tool_summary(block.name, block.input)})

    @property
    def draft(self) -> str:
        final = (self.result.result if self.result is not None else None) or ''
        return final if final.strip() else (self.texts[-1] if self.texts else '')
