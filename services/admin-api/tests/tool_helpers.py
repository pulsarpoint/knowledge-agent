"""Catalog tools as the model gets them (tools/sdk.py), for tests that call one tool directly. The run context
offers every catalog tool unless the test sets context['tools'] itself."""
import dataclasses

from agents import FunctionTool

from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import function_tool


def tools(*names: str) -> list[FunctionTool]:
    built = []
    for name in names:
        spec = CATALOG[name]
        sdk_tool = function_tool(spec, spec.usage)

        async def invoke(ctx, raw, _invoke=sdk_tool.on_invoke_tool):
            ctx.context.setdefault('tools', frozenset(CATALOG))
            return await _invoke(ctx, raw)
        built.append(dataclasses.replace(sdk_tool, on_invoke_tool=invoke))
    return built


def tool(name: str) -> FunctionTool:
    return tools(name)[0]
