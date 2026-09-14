"""Validate model diagrams using the same Mermaid parser as the browser."""
import asyncio
import json
import os
import re
from pathlib import Path

from markdown_it import MarkdownIt


class MermaidValidator:
    def __init__(self):
        self.checked: dict[str, dict] = {}

    async def validate(self, chart: str) -> dict:
        chart = chart.strip()
        if not chart or len(chart) > 40000:
            return {'valid': False, 'error': 'Use a nonempty diagram of at most 40,000 characters.'}
        if chart in self.checked:
            return self.checked[chart]
        script = os.environ.get('MERMAID_VALIDATOR_SCRIPT') or str(
            Path(__file__).resolve().parents[4] / 'packages/mermaid-validator/validate.mjs')
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                'node', '--max-old-space-size=256', script,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # The diagram parser does not need any application credentials.
                env={'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8'})
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(chart).encode()), timeout=8)
            if process.returncode != 0:
                raise ValueError('Parser failed')
            result = json.loads(stdout)
            if not isinstance(result, dict) or type(result.get('valid')) is not bool:
                raise ValueError('Invalid parser response')
        except (OSError, ValueError, TimeoutError):
            result = {'valid': False, 'error': 'Diagram validation is unavailable. Answer in prose instead.'}
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        self.checked[chart] = result
        return result

    async def validate_answer(self, text: str) -> str:
        """Keep a usable answer even if a model skips the validation tool."""
        lines = text.splitlines(keepends=True)
        diagrams = [token for token in MarkdownIt().parse(text)
                    if token.type == 'fence' and token.info.strip().lower() == 'mermaid']
        for number, token in enumerate(diagrams):
            result = await self.validate(token.content) if number < 8 else {'valid': False}
            if not result['valid'] and token.map is not None:
                start = token.map[0]
                # Keep the exact source as inert code; never let a broken diagram fail the answer.
                lines[start] = re.sub(r'(`{3,}|~{3,})\s*mermaid\b', r'\1text', lines[start], flags=re.IGNORECASE)
        return ''.join(lines)
