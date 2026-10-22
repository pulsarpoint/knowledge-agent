"""Diagrams of a system graph view (inventory_map.build_view): Mermaid and D2 text, and an SVG drawn by the d2 binary.
Keys are generated (n1, n2, …); names only ever appear as escaped, quoted labels, so a node name cannot add shapes,
edges, variables or imports. No icons, links or imports are emitted, so d2 never fetches anything."""

import shutil
import subprocess
from collections import defaultdict

LAYOUTS = ('tala', 'elk', 'dagre')
SVG_TIMEOUT = 20
MAX_ERROR = 2000
SHAPES = {'database': 'cylinder', 'table': 'cylinder', 'queue': 'queue', 'person': 'person', 'actor': 'person'}
MERMAID_SHAPES = {'cylinder': ('[(', ')]')}
RELATIONS = ('depends_on', 'part_of', 'built_from')  # service relations: drawn dashed, connections solid
MERMAID_ESCAPES = {'#': '#35;', '"': '#34;', '<': '#60;', '>': '#62;', '&': '#38;', '`': '#96;'}


class RenderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _flat(text: str) -> str:
    """One line: control characters (newlines, tabs, bells) become spaces."""
    return ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in text)


def d2_string(text: str) -> str:
    return '"' + _flat(text).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def mermaid_string(text: str) -> str:
    return '"' + ''.join(MERMAID_ESCAPES.get(char, char) for char in _flat(text)) + '"'


def _shape(node: dict) -> str:
    return SHAPES.get(node['kind'].strip().lower(), 'rectangle')


def _label(edge: dict) -> str:
    kind = edge['type'].replace('_', ' ')
    return kind if edge['count'] == 1 else f'{kind} ×{edge["count"]}'


def _keys(view: dict) -> dict:
    return {node['id']: f'n{index}' for index, node in enumerate([*view['nodes'], *view['boundary']], start=1)}


def _children(view: dict) -> dict:
    children = defaultdict(list)
    for node in view['nodes']:
        children[node['parent_id']].append(node)
    return children


def d2(view: dict) -> str:
    key, children = _keys(view), _children(view)
    lines, where = [], {}

    def place(node: dict, path: str, indent: str) -> None:
        name = f'{path}.{key[node["id"]]}' if path else key[node['id']]
        where[node['id']] = name
        inner = children.get(node['id'], [])
        if inner:
            lines.append(f'{indent}{key[node["id"]]}: {d2_string(node["name"])} {{')
            for child in inner:
                place(child, name, indent + '  ')
            lines.append(f'{indent}}}')
        else:
            lines.append(f'{indent}{key[node["id"]]}: {d2_string(node["name"])} {{shape: {_shape(node)}}}')

    for node in children.get(None, []):
        place(node, '', '')
    if view['boundary']:
        lines += ['outside: "Outside" {', '  style.stroke-dash: 4']
        for node in view['boundary']:
            where[node['id']] = f'outside.{key[node["id"]]}'
            lines.append(f'  {key[node["id"]]}: {d2_string(node["name"])} {{style.stroke-dash: 4}}')
        lines.append('}')
    for edge in view['edges']:
        style = ' {style.stroke-dash: 3}' if edge['type'] in RELATIONS else ''
        lines.append(f'{where[edge["from"]]} -> {where[edge["to"]]}: {d2_string(_label(edge))}{style}')
    return ''.join(line + '\n' for line in lines)


def mermaid(view: dict) -> str:
    key, children = _keys(view), _children(view)
    lines = ['flowchart LR']

    def place(node: dict, indent: str) -> None:
        inner = children.get(node['id'], [])
        if inner:
            lines.append(f'{indent}subgraph {key[node["id"]]}[{mermaid_string(node["name"])}]')
            for child in inner:
                place(child, indent + '  ')
            lines.append(f'{indent}end')
        else:
            start, end = MERMAID_SHAPES.get(_shape(node), ('[', ']'))
            lines.append(f'{indent}{key[node["id"]]}{start}{mermaid_string(node["name"])}{end}')

    for node in children.get(None, []):
        place(node, '  ')
    if view['boundary']:
        lines.append('  subgraph outside["Outside"]')
        lines += [f'    {key[node["id"]]}[{mermaid_string(node["name"])}]' for node in view['boundary']]
        lines.append('  end')
    for edge in view['edges']:
        arrow = '-.->' if edge['type'] in RELATIONS else '-->'
        lines.append(f'  {key[edge["from"]]} {arrow}|{mermaid_string(_label(edge))}| {key[edge["to"]]}')
    if view['boundary']:
        lines.append('  classDef boundary stroke-dasharray: 4 4')
        lines.append('  class ' + ','.join(key[node['id']] for node in view['boundary']) + ' boundary')
    return ''.join(line + '\n' for line in lines)


def svg(text: str, layout: str) -> bytes:
    if layout not in LAYOUTS:
        raise RenderError(400, 'Choose tala, elk or dagre.')
    binary = shutil.which('d2')
    if binary is None:
        raise RenderError(503, 'd2 is not installed.')
    try:
        done = subprocess.run(
            [binary, f'--layout={layout}', '-', '-'],
            input=text.encode(),
            capture_output=True,
            timeout=SVG_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RenderError(502, f'd2 took longer than {SVG_TIMEOUT} s.') from None
    if done.returncode != 0:
        message = done.stderr.decode(errors='replace').strip() or f'd2 exited with {done.returncode}.'
        raise RenderError(502, message[:MAX_ERROR])
    return done.stdout
