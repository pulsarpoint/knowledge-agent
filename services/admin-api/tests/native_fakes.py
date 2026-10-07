"""Stand-ins for the Agents SDK objects a native turn's hooks receive (model responses, tool contexts), shaped like the
real ones (test_native_session_events checks the fields)."""
import json
from types import SimpleNamespace

from claude_code_fakes import Recorder as _Recorder


class Recorder(_Recorder):
    """Collects events like RunRecorder.add and counts flushes."""

    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def reasoning(text: str):
    return SimpleNamespace(type='reasoning', summary=[SimpleNamespace(text=text)], content=[])


def message(text: str):
    return SimpleNamespace(type='message', content=[SimpleNamespace(text=text)])


def call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(type='function_call', call_id=call_id, name=name, arguments=json.dumps(arguments))


def response(*items, input_tokens: int = 0, output_tokens: int = 0, cached: int = 0):
    """A ModelResponse: its output items and its Usage."""
    return SimpleNamespace(output=list(items), usage=SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached)))
