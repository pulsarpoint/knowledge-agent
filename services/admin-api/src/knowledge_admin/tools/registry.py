"""The @tool decorator: a tool is a plain async function whose signature (after the run context) is its argument
schema and whose docstring is the usage text the model reads. Imports nothing from knowledge_admin.capabilities, so
capability modules can use it; tools/catalog.py adds the attributes from tools.json, tools/sdk.py binds to the SDK."""

import inspect
import typing
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model


@dataclass(frozen=True)
class Tool:
    name: str
    usage: str
    arguments: type[BaseModel]
    run: Callable[[Any, BaseModel], Awaitable[str]]


REGISTRY: dict[str, Tool] = {}
_target: list[dict[str, Tool]] = [REGISTRY]


@contextmanager
def register_into(registry: dict[str, Tool]) -> Iterator[dict[str, Tool]]:
    """Register into another dict for the duration (tests)."""
    _target.append(registry)
    try:
        yield registry
    finally:
        _target.pop()


def camel(name: str) -> str:
    return ''.join(part.capitalize() for part in name.split('_'))


def _arguments(name: str, function: Callable[..., Any]) -> type[BaseModel]:
    hints = typing.get_type_hints(function, include_extras=True)
    fields: dict[str, Any] = {}
    for parameter in list(inspect.signature(function).parameters.values())[1:]:  # the first is the run context
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise ValueError(f'Tool {name}: *args and **kwargs needs arguments=.')
        if parameter.name not in hints:
            raise ValueError(f'Tool {name}: parameter {parameter.name} has no annotation.')
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (hints[parameter.name], default)
    return create_model(camel(name), __config__=ConfigDict(extra='forbid'), **fields)


def tool(
    fn: Callable[..., Awaitable[str]] | None = None,
    *,
    name: str | None = None,
    usage: str | None = None,
    arguments: type[BaseModel] | None = None,
):
    def register(function: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        tool_name = name or function.__name__
        registry = _target[-1]
        if tool_name in registry:
            raise ValueError(f'Tool {tool_name} is registered twice.')
        text = usage if usage is not None else inspect.cleandoc(function.__doc__ or '')
        if not text:
            raise ValueError(f'Tool {tool_name} has no usage text: give it a docstring or usage=.')
        model = arguments if arguments is not None else _arguments(tool_name, function)

        async def run(context: Any, value: BaseModel) -> str:
            return await function(context, **value.model_dump())

        registry[tool_name] = Tool(tool_name, text, model, run)
        return function

    return register(fn) if fn is not None else register
