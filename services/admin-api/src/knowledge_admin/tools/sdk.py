"""Bind catalog tools to the OpenAI Agents SDK. The only module that knows how a tool reaches the model; another
framework would replace this file, not the catalog."""
import logging
from typing import Any

from agents import FunctionTool
from agents.tracing import SpanError, get_current_span
from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext

from knowledge_admin.agent_activity import redact
from knowledge_admin.tools.catalog import ToolSpec

logger = logging.getLogger(__name__)
NOT_OFFERED = 'Tool {name} is not available in this run.'


def function_tool(spec: ToolSpec, usage: str) -> FunctionTool:
    """One SDK function tool: the usage text is its description and the argument model its strict JSON schema.

    Invocation refuses a tool missing from context['tools'] (the run's offered tools, a backstop), validates the
    arguments and runs the catalog function. Errors are logged and set on the current trace span with the run's
    credentials redacted, as decorated function tools did, and go back to the model in the SDK's default error text,
    also redacted; cancellation propagates."""
    async def invoke(ctx: ToolContext[Any], raw: str) -> str:
        try:
            if spec.name not in ctx.context.get('tools', ()):
                raise ValueError(NOT_OFFERED.format(name=spec.name))
            arguments = spec.arguments.model_validate_json(raw or '{}')
            return await spec.run(ctx.context, arguments)
        except Exception as exc:
            error = redact(str(exc), ctx.context.get('credentials', {}).values())
            span = get_current_span()
            if span is not None:
                span.set_error(SpanError(message='Error running tool (non-fatal)',
                                         data={'tool_name': spec.name, 'error': error}))
            logger.error('Tool %s failed: %s', spec.name, error)
            return f'An error occurred while running the tool. Please try again. Error: {error}'

    return FunctionTool(name=spec.name, description=usage,
                        params_json_schema=ensure_strict_json_schema(spec.arguments.model_json_schema()),
                        on_invoke_tool=invoke, strict_json_schema=True)
