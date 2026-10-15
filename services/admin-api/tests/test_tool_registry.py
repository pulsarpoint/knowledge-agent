"""The tool decorator: the function's signature is the argument schema and its docstring the usage text."""

import asyncio
from typing import Annotated, Literal

import pytest
from knowledge_admin.tools.registry import REGISTRY, Tool, camel, register_into, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError


def test_function_is_returned_unchanged():
    with register_into({}):

        async def list_things(context, path: str) -> str:
            """List things."""
            return path

        assert tool(list_things) is list_things


def test_schema_from_annotated_fields_and_defaults():
    found: dict[str, Tool] = {}
    with register_into(found):

        @tool
        async def read_thing(
            context,
            path: Annotated[str, Field(description='The path.')],
            limit: Annotated[int, Field(ge=1, description='At most.')] = 5,
            mode: Literal['a', 'b'] = 'a',
            note: str | None = None,
        ) -> str:
            """Read a thing.

            Second paragraph."""
            return path

    spec = found['read_thing']
    assert spec.usage == 'Read a thing.\n\nSecond paragraph.'
    schema = spec.arguments.model_json_schema()
    assert schema['title'] == 'ReadThing'
    assert list(schema['properties']) == ['path', 'limit', 'mode', 'note']
    assert schema['required'] == ['path']
    assert schema['properties']['path']['description'] == 'The path.'
    assert schema['properties']['limit'] == {
        'default': 5,
        'description': 'At most.',
        'minimum': 1,
        'title': 'Limit',
        'type': 'integer',
    }
    assert schema['additionalProperties'] is False
    with pytest.raises(ValidationError):
        spec.arguments.model_validate({'path': 'x', 'other': 1})


def test_run_passes_the_validated_arguments_as_keywords():
    found: dict[str, Tool] = {}
    with register_into(found):

        @tool
        async def echo(context, text: str, times: int = 1) -> str:
            """Echo."""
            return f'{context["who"]}:{text * times}'

    spec = found['echo']
    assert asyncio.run(spec.run({'who': 'me'}, spec.arguments(text='ab', times=2))) == 'me:abab'


def test_string_annotations_resolve():
    found: dict[str, Tool] = {}
    with register_into(found):

        @tool
        async def later(context, count: 'int' = 3) -> str:
            """Later."""
            return ''

    assert found['later'].arguments.model_json_schema()['properties']['count']['type'] == 'integer'


def test_name_usage_and_arguments_overrides():
    class Given(BaseModel):
        model_config = ConfigDict(extra='forbid')
        value: str

    found: dict[str, Tool] = {}
    with register_into(found):

        @tool(name='renamed', usage='Shared text.', arguments=Given)
        async def anything(context, **value) -> str:
            return value['value']

    spec = found['renamed']
    assert (spec.usage, spec.arguments) == ('Shared text.', Given)


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        ('async def no_doc(context, a: str) -> str:\n    return a', 'no usage text'),
        ('async def kw(context, **value) -> str:\n    """Kw."""\n    return ""', 'needs arguments='),
        ('async def bare(context, a) -> str:\n    """Bare."""\n    return a', 'has no annotation'),
    ],
)
def test_import_time_errors(source, message):
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - compiles a source snippet on purpose
    function = next(value for key, value in namespace.items() if callable(value))
    with register_into({}), pytest.raises(ValueError, match=message):
        tool(function)


def test_duplicate_name_raises():
    with register_into({}):

        @tool
        async def twice(context) -> str:
            """Once."""
            return ''

        with pytest.raises(ValueError, match='registered twice'):
            tool(name='twice', usage='Again.')(twice)


def test_camel():
    assert [camel(n) for n in ('list_files', 'ssh_run', 'report_not_covered')] == [
        'ListFiles',
        'SshRun',
        'ReportNotCovered',
    ]


def test_the_global_registry_is_untouched_by_scratch_registration():
    before = dict(REGISTRY)
    with register_into({}):

        @tool
        async def scratch(context) -> str:
            """Scratch."""
            return ''

    assert REGISTRY == before
