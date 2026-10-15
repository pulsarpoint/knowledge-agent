"""The catalog is the fixture's attributes joined with the registered functions; disagreements fail at import."""

import pytest
from knowledge_admin.tools.catalog import CATALOG, load_catalog
from knowledge_admin.tools.fixture import load_fixture
from knowledge_admin.tools.registry import REGISTRY, Tool
from pydantic import BaseModel


async def _run(context, value):
    return ''


def a_tool(name):
    return Tool(name, 'Usage.', BaseModel, _run)


ENTRY = {'name': 'one', 'group': 'code', 'label': 'One', 'summary': 'One tool.', 'needs': None}


def test_catalog_is_in_fixture_order_and_every_registered_tool_is_in_it():
    assert list(CATALOG) == [entry['name'] for entry in load_fixture()]
    assert set(CATALOG) == set(REGISTRY)


def test_an_entry_without_a_function_raises():
    with pytest.raises(ValueError, match='no registered function: two'):
        load_catalog([ENTRY, dict(ENTRY, name='two')], {'one': a_tool('one')})


def test_a_function_without_an_entry_raises():
    with pytest.raises(ValueError, match='not in tools.json: two'):
        load_catalog([ENTRY], {'one': a_tool('one'), 'two': a_tool('two')})


def test_unknown_need_in_fixture_raises():
    with pytest.raises(ValueError, match='unknown needs'):
        load_catalog([dict(ENTRY, needs='wishes')], {'one': a_tool('one')})


def test_unknown_group_in_fixture_raises():
    with pytest.raises(ValueError, match='unknown group'):
        load_catalog([dict(ENTRY, group='toys')], {'one': a_tool('one')})


def test_duplicate_entry_raises():
    with pytest.raises(ValueError, match='twice'):
        load_catalog([ENTRY, ENTRY], {'one': a_tool('one')})
