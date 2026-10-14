"""Send native Agents SDK traces to Phoenix, with secrets removed before export."""

import logging
import os
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from agents import set_trace_processors
from opentelemetry import trace as otel
from openinference.instrumentation import OITracer, TraceConfig
from openinference.instrumentation.openai_agents._processor import OpenInferenceTracingProcessor

from knowledge_admin.sessions.activity import redact

# This application exports to its configured Phoenix instance, not OpenAI's backend.
set_trace_processors([])
_secrets: ContextVar[tuple[str, ...]] = ContextVar('agent_trace_secrets', default=())
logger = logging.getLogger(__name__)


class RedactTraceConfig(TraceConfig):
    def mask(self, key, value, *, externalize=True):
        value = super().mask(key, value, externalize=externalize)
        if isinstance(value, str):
            return redact(value, _secrets.get())[:65536]
        if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
            return [redact(item, _secrets.get())[:65536] for item in value]
        return value


class RedactingProcessor(OpenInferenceTracingProcessor):
    """Keep the vendor's span mapping; sanitize names and errors before it exports."""

    def on_span_start(self, span):
        if isinstance(getattr(span.span_data, 'name', None), str):
            span.span_data.name = redact(span.span_data.name, _secrets.get())
        super().on_span_start(span)

    def on_span_end(self, span):
        if span.error:
            span.set_error({'message': 'Agent step failed. Check its configuration and connection.'})
        super().on_span_end(span)


_registered = False


def register(endpoint: str | None, api_key: str | None) -> bool:
    """Register the OTel tracer provider against Phoenix once; failure is a warning, never an error."""
    global _registered
    if _registered:
        return True
    if not endpoint:
        return False
    try:
        import phoenix.otel

        # The project name predates the admin API; existing traces live under it.
        phoenix.otel.register(endpoint=endpoint, project_name='docs-wiki', batch=True, api_key=api_key or None,
                              auto_instrument=False, set_global_tracer_provider=True, verbose=False)
    except Exception:
        logger.warning('Phoenix tracing setup failed; continuing untraced.', exc_info=True)
        return False
    _registered = True
    return True


def setup():
    if register(os.environ.get('DOCS_WIKI_PHOENIX_ENDPOINT'), os.environ.get('DOCS_WIKI_PHOENIX_API_KEY')):
        try:
            instrument(otel.get_tracer_provider())
        except Exception:
            logger.warning('Agents SDK tracing could not be initialized.')


def instrument(provider):
    tracer = OITracer(provider.get_tracer('knowledge_admin.agents'), config=RedactTraceConfig())
    set_trace_processors([RedactingProcessor(tracer)])


@contextmanager
def trace_secrets(values: Iterable[str]):
    """Redact `values` from traces in this block, together with an enclosing run's (a subagent's traces are redacted
    with its parent's secrets too)."""
    token = _secrets.set(tuple(dict.fromkeys((*_secrets.get(), *values))))
    try:
        yield
    finally:
        _secrets.reset(token)
