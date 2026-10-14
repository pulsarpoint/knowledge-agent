"""Diagram text from a system graph view: generated keys, labels that are always literal, and SVG through d2."""
import html
import re
import shutil
import subprocess

import pytest

from knowledge_admin.servers import system_graph_render as render


def node(id, name, parent=None, kind='module'):
    return {'id': id, 'parent_id': parent, 'layer': 'software', 'kind': kind, 'name': name, 'description': '',
            'child_count': 0, 'expandable': False, 'internal_edges': 0, 'documents': []}


VIEW = {
    'layer': 'software', 'depth': 2, 'root': None, 'breadcrumb': [],
    'nodes': [node('events', 'Event processor'), node('match', 'matching', 'events'), node('store', 'events table', kind='database')],
    'boundary': [{'id': 'prod', 'name': 'prod-1', 'kind': 'server', 'layer': 'infrastructure'}],
    'edges': [{'from': 'match', 'to': 'store', 'type': 'writes', 'count': 3, 'edge_ids': ['a', 'b', 'c']},
              {'from': 'prod', 'to': 'events', 'type': 'instance_of', 'count': 1, 'edge_ids': ['d']}],
}


def test_d2_nests_containers_and_styles_the_boundary():
    assert render.d2(VIEW) == '\n'.join([
        'n1: "Event processor" {',
        '  n2: "matching" {shape: rectangle}',
        '}',
        'n3: "events table" {shape: cylinder}',
        'outside: "Outside" {',
        '  style.stroke-dash: 4',
        '  n4: "prod-1" {style.stroke-dash: 4}',
        '}',
        'n1.n2 -> n3: "writes ×3"',
        'outside.n4 -> n1: "instance of"',
    ]) + '\n'


def test_mermaid_uses_subgraphs_and_marks_the_boundary():
    assert render.mermaid(VIEW) == '\n'.join([
        'flowchart LR',
        '  subgraph n1["Event processor"]',
        '    n2["matching"]',
        '  end',
        '  n3[("events table")]',
        '  subgraph outside["Outside"]',
        '    n4["prod-1"]',
        '  end',
        '  n2 -->|"writes ×3"| n3',
        '  n4 -->|"instance of"| n1',
        '  classDef boundary stroke-dasharray: 4 4',
        '  class n4 boundary',
    ]) + '\n'


@pytest.mark.parametrize('kind, shape', [('table', 'cylinder'), ('Database', 'cylinder'), ('queue', 'queue'),
                                         ('person', 'person'), ('actor', 'person'), ('service', 'rectangle')])
def test_shapes_follow_the_kind(kind, shape):
    view = {**VIEW, 'nodes': [node('x', 'x', kind=kind)], 'boundary': [], 'edges': []}
    assert render.d2(view) == f'n1: "x" {{shape: {shape}}}\n'


def test_an_empty_view():
    empty = {**VIEW, 'nodes': [], 'boundary': [], 'edges': []}
    assert (render.d2(empty), render.mermaid(empty)) == ('', 'flowchart LR\n')


def test_hostile_names_are_literal_labels():
    assert render.d2_string('a"; x -> y') == '"a\\"; x -> y"'
    assert render.d2_string('${x} costs $5 } # done') == '"\\${x} costs \\$5 } # done"'
    assert render.d2_string('c:\\path') == '"c:\\\\path"'
    assert render.d2_string('two\nlines\tand\r\x07bell') == '"two lines and  bell"'
    assert render.mermaid_string('a"; x -> y') == '"a#34;; x -#62; y"'
    assert render.mermaid_string('<b>&`#1;') == '"#60;b#62;#38;#96;#35;1;"'
    names = ['a"; x -> y', '}', '${x}', '# hash', 'two\nlines', 'end']
    view = {**VIEW, 'nodes': [node(f'h{i}', name) for i, name in enumerate(names)], 'boundary': [],
            'edges': [{'from': 'h0', 'to': 'h1', 'type': 'calls', 'count': 1, 'edge_ids': ['x']}]}
    d2_lines = render.d2(view).splitlines()
    assert len(d2_lines) == len(names) + 1 and sum('->' in line.split(':')[0] for line in d2_lines) == 1
    mermaid_lines = render.mermaid(view).splitlines()
    assert len(mermaid_lines) == len(names) + 2 and sum('-->' in line for line in mermaid_lines) == 1


def test_svg_runs_d2_with_the_layout(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=b'<svg/>', stderr=b'success: compiled')
    monkeypatch.setattr(render.shutil, 'which', lambda name: '/usr/local/bin/d2')
    monkeypatch.setattr(render.subprocess, 'run', run)
    assert render.svg('a -> b\n', 'elk') == b'<svg/>'
    assert seen['args'] == ['/usr/local/bin/d2', '--layout=elk', '-', '-']
    assert (seen['input'], seen['timeout'], seen['capture_output'], seen['check']) == (b'a -> b\n', 20, True, False)


def test_svg_without_d2(monkeypatch):
    monkeypatch.setattr(render.shutil, 'which', lambda name: None)
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert (raised.value.status, str(raised.value)) == (503, 'd2 is not installed.')


def test_svg_failures(monkeypatch):
    monkeypatch.setattr(render.shutil, 'which', lambda name: '/usr/local/bin/d2')
    monkeypatch.setattr(render.subprocess, 'run',
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout=b'', stderr=b'err: ' + b'x' * 3000))
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert raised.value.status == 502 and str(raised.value) == ('err: ' + 'x' * 3000)[:2000]

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    monkeypatch.setattr(render.subprocess, 'run', slow)
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert (raised.value.status, str(raised.value)) == (502, 'd2 took longer than 20 s.')
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'neato')
    assert (raised.value.status, str(raised.value)) == (400, 'Choose tala, elk or dagre.')


@pytest.mark.skipif(shutil.which('d2') is None, reason='d2 is not installed')
@pytest.mark.parametrize('layout', ['tala', 'elk', 'dagre'])
def test_real_render_keeps_hostile_labels(layout):
    names = ['a"; x -> y', '${x} costs $5', '} brace', '# hash', '[x] {y} @import']
    view = {**VIEW, 'nodes': [node(f'h{i}', name) for i, name in enumerate(names)], 'boundary': [],
            'edges': [{'from': 'h0', 'to': 'h1', 'type': 'calls', 'count': 2, 'edge_ids': ['x', 'y']}]}
    svg = render.svg(render.d2(view), layout).decode()
    texts = {html.unescape(text) for text in re.findall(r'<text[^>]*>(.*?)</text>', svg)}
    assert set(names) <= texts and 'calls ×2' in texts
