"""Bind catalog tools to the OpenAI Agents SDK. The only module that knows how a tool reaches the model; another
framework would replace this file, not the catalog."""
from typing import Any

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import default_tool_error_function
from agents.tool_context import ToolContext

from knowledge_admin.tools.catalog import ToolSpec

NOT_OFFERED = 'Tool {name} is not available in this run.'


def function_tool(spec: ToolSpec, usage: str) -> FunctionTool:
    """One SDK function tool: the usage text is its description and the argument model its strict JSON schema.

    Invocation validates the arguments, refuses a tool missing from context['tools'] (the run's offered tools, a
    backstop) and runs the catalog function. Errors go back to the model as the SDK's default error text, as with
    decorated function tools; cancellation propagates."""
    async def invoke(ctx: ToolContext[Any], raw: str) -> str:
        try:
            arguments = spec.arguments.model_validate_json(raw or '{}')
            if spec.name not in ctx.context.get('tools', ()):
                raise ValueError(NOT_OFFERED.format(name=spec.name))
            return await spec.run(ctx.context, arguments)
        except Exception as exc:
            return default_tool_error_function(ctx, exc)

    return FunctionTool(name=spec.name, description=usage,
                        params_json_schema=ensure_strict_json_schema(spec.arguments.model_json_schema()),
                        on_invoke_tool=invoke, strict_json_schema=True)
