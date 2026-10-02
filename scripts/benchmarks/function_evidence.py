"""Extract small Go analysis units from frozen source; never use graph targets."""

import re
from pathlib import PurePosixPath

from rlm_evidence import SourceEvidence


def package_files(evidence: SourceEvidence, path: str) -> list[str]:
    package = next(m['text'] for m in evidence.scan_go(path) if m['ruleId'] == 'package')
    return [candidate for candidate in sorted(evidence.context['files'])
            if candidate.endswith('.go') and not candidate.endswith('_test.go')
            and PurePosixPath(candidate).parent == PurePosixPath(path).parent
            and any(m['ruleId'] == 'package' and m['text'] == package for m in evidence.scan_go(candidate))]


def find_function(evidence: SourceEvidence, path: str, name: str, receiver: str | None) -> dict:
    """Exact file/name/receiver lookup. Build variants and overloads never pick the first match."""
    matches = []
    for match in evidence.scan_go(path):
        if match['ruleId'] != 'definition':
            continue
        variables = match['metaVariables']['single']
        if variables['NAME']['text'] != name:
            continue
        actual_receiver = variables.get('RECEIVER', {}).get('text')
        if receiver is None and actual_receiver is None:
            matches.append(match)
        elif receiver is not None and actual_receiver is not None:
            # Only the ordinary named receiver form is supported here. Generic
            # receiver parameters need type resolution, not a same-name guess.
            parsed = re.fullmatch(r'\(\s*\w+\s+\*?(\w+)\s*\)', actual_receiver)
            if parsed is not None and parsed[1] == receiver:
                matches.append(match)
    if len(matches) != 1:
        raise ValueError(f'Function missing or ambiguous: {path}, {receiver or ""}.{name}')
    return matches[0]


def collect_function_evidence(evidence: SourceEvidence, task: dict, resources: list[dict]) -> dict:
    """Include selected bodies, local callee signatures and referenced declarations.

    Direct functions and simple constructor-bound methods can be requested by
    dependency ID. Unknown types stay unresolved; graph targets are never used.
    """
    selected: dict[str, set[int]] = {}
    definitions = []
    dependencies = {}

    def include(path: str, match: dict) -> None:
        start = match['range']['start']['line'] + 1
        end = match['range']['end']['line'] + 1
        selected.setdefault(path, set()).update(range(start, end + 1))

    for resource in resources:
        path = resource['path']
        function = find_function(evidence, path, resource['function'], resource.get('receiver'))
        include(path, function)
        definitions.append((path, function))
        package = package_files(evidence, path)
        body = function['range']['byteOffset']
        names = {m['text'] for m in evidence.scan_go(path) if m['ruleId'] == 'identifier'
                 and body['start'] <= m['range']['byteOffset']['start'] < body['end']}
        declarations = []
        for file in package:
            syntax = evidence.scan_go(file)
            functions = [m['range']['byteOffset'] for m in syntax if m['ruleId'] == 'definition']
            for declaration in (m for m in syntax if m['ruleId'] == 'declaration'):
                bounds = declaration['range']['byteOffset']
                if any(f['start'] <= bounds['start'] < f['end'] for f in functions):
                    continue  # Local declarations are already inside their complete function.
                declared = {m['metaVariables']['single']['NAME']['text'] for m in syntax
                            if m['ruleId'] == 'declared-name'
                            and bounds['start'] <= m['range']['byteOffset']['start'] < bounds['end']}
                referenced = {m['text'] for m in syntax if m['ruleId'] == 'identifier'
                              and bounds['start'] <= m['range']['byteOffset']['start'] < bounds['end']}
                declarations.append((file, declaration, declared, referenced))
        pending = list(declarations)
        while pending:
            needed = [d for d in pending if d[2] & names]
            if not needed:
                break
            for file, declaration, declared, referenced in needed:
                include(file, declaration)
                names.update(referenced)
                pending.remove((file, declaration, declared, referenced))

    for path, function in definitions:
        body = function['range']['byteOffset']
        for call in evidence.scan_go(path):
            if call['ruleId'] != 'call' or not (
                body['start'] <= call['range']['byteOffset']['start'] < body['end']
            ):
                continue
            expression = call['metaVariables']['single']['CALLEE']['text']
            key = f"{path}:{body['start']}:{expression}"
            if key in dependencies:
                continue  # One requestable implementation per expression in this function.
            target, reason = resolve_direct_call(evidence, path, function, expression)
            supporting, proof = [], []
            if target is None:
                resolved = resolve_constructor_method(evidence, path, function, call)
                if resolved is not None:
                    target, reason = resolved['target'], resolved['boundary']
                    supporting, proof = resolved['supporting'], resolved['proof']
            signature = None
            if target is not None:
                declaration = find_function(evidence, target['path'], target['function'], target.get('receiver'))
                source = declaration['text'].encode('utf-8')
                callee_body = declaration['metaVariables']['single'].get('BODY')
                if callee_body is not None:
                    source = source[:callee_body['range']['byteOffset']['start'] - declaration['range']['byteOffset']['start']]
                header = source.decode('utf-8').rstrip()
                start_line = declaration['range']['start']['line'] + 1
                signature = {'path': target['path'], 'start_line': start_line,
                             'end_line': start_line + len(header.splitlines()) - 1,
                             'text': header, 'body_included': False}
            dependencies[key] = {'expression': expression, 'path': path,
                'line': call['range']['start']['line'] + 1, 'target': target, 'boundary': reason,
                'supporting': supporting, 'resolution_evidence': proof, 'signature': signature}

    for path in list(selected):
        for match in evidence.scan_go(path):
            if match['ruleId'] in ('package', 'import'):
                include(path, match)
    fragments = []
    for i, (path, lines) in enumerate(sorted(selected.items()), 1):
        source = evidence.context['files'][path].splitlines()
        ordered = sorted(lines)
        for previous, following in zip(ordered, ordered[1:]):
            if following > previous + 1 and all(not source[n - 1].strip() for n in range(previous + 1, following)):
                lines.update(range(previous + 1, following))
        fragments.append({'id': f'S{i}', 'path': path, 'start_line': min(lines), 'end_line': max(lines),
                          'lines': [{'line': n, 'text': source[n - 1]} for n in sorted(lines)]})
    return {'task': task, 'fragments': fragments,
            'observations': {'dependencies': {f'D{i}': record for i, record in enumerate(dependencies.values(), 1)}},
            'gaps': ['Only supplied function bodies and declarations were extracted. No graph targets are used.',
                     'Local callee signatures establish declared parameters and results, not implementation behavior. '
                     'Signatures are dependency observations; their bodies require a separate request.',
                     'Dependency locations are syntax-based, not proof of runtime activation.',
                     'Only unambiguous direct calls and simple constructor-bound methods resolve; '
                     'interfaces, indirect calls and missing packages remain boundaries.']}


def resolve_direct_call(evidence: SourceEvidence, path: str, function: dict, expression: str) -> tuple[dict | None, str | None]:
    """Resolve direct package calls only; uncertain receivers remain explicit boundaries."""
    if re.fullmatch(r'\w+(?:\.\w+)?', expression) is None:
        return None, 'Indirect expression; type resolution required'
    parts = expression.split('.')
    qualifier = parts[0]
    # A local name or parameter can hide a package alias or package function.
    # Reject uncertain bindings instead of silently resolving to a global name.
    bounds = function['range']['byteOffset']
    for match in evidence.scan_go(path):
        if not bounds['start'] <= match['range']['byteOffset']['start'] < bounds['end']:
            continue
        if match['ruleId'] == 'binding':
            bound = match['metaVariables']['single']['BOUND']['text']
        elif match['ruleId'] == 'parameter':
            bound = match['text']  # Conservative for compound/generic parameter types.
        elif match['ruleId'] == 'declared-name':
            bound = match['text'].split('=', 1)[0]
        else:
            continue
        if qualifier in re.findall(r'\b\w+\b', bound):
            return None, 'Local binding or parameter; receiver/function-value resolution required'
    directory = PurePosixPath(path).parent
    if len(parts) == 2:
        imports = {}
        for match in evidence.scan_go(path):
            if match['ruleId'] != 'import-spec':
                continue
            spec = re.fullmatch(r'\s*(?:(\w+|[._])\s+)?["`]([^"`]+)["`]\s*', match['text'])
            if spec is not None:
                imports[spec[1] or spec[2].rsplit('/', 1)[-1]] = spec[2]
        imported = imports.get(qualifier)
        project = path.split('/')[0]
        module_source = evidence.context['files'].get(f'{project}/go.mod', '')
        module = re.search(r'^module\s+(\S+)', module_source, re.M)
        if imported is None or module is None or not imported.startswith(module[1] + '/'):
            return None, 'Receiver or external package; no resolved local function'
        directory = PurePosixPath(project, imported[len(module[1]) + 1:])
    candidates = []
    current_package = next(m['text'] for m in evidence.scan_go(path) if m['ruleId'] == 'package')
    for candidate in sorted(evidence.context['files']):
        if not candidate.endswith('.go') or candidate.endswith('_test.go') or PurePosixPath(candidate).parent != directory:
            continue
        if len(parts) == 1 and not any(m['ruleId'] == 'package' and m['text'] == current_package
                                     for m in evidence.scan_go(candidate)):
            continue
        for match in evidence.scan_go(candidate):
            if match['ruleId'] != 'definition':
                continue
            variables = match['metaVariables']['single']
            if variables['NAME']['text'] == parts[-1] and 'RECEIVER' not in variables:
                candidates.append({'path': candidate, 'function': parts[-1]})
    if len(candidates) != 1:
        return None, 'No unique direct function in the selected package (including build variants)'
    return candidates[0], None


def resolve_constructor_method(evidence: SourceEvidence, path: str, function: dict, call: dict) -> dict | None:
    """Follow a single root-scope `value := Constructor(...)` with a concrete result.

    This resolves the static method identity, not invocation success, reachability,
    or dynamic object state. Rebindings/shadows and unsupported types fail closed.
    """
    expression = call['metaVariables']['single']['CALLEE']['text']
    parts = expression.split('.')
    if len(parts) != 2 or any(re.fullmatch(r'\w+', p) is None for p in parts):
        return None
    variable, method_name = parts
    bounds = function['range']['byteOffset']
    body = function['metaVariables']['single'].get('BODY')
    if body is None:
        return None
    syntax = evidence.scan_go(path)
    local = [m for m in syntax if bounds['start'] <= m['range']['byteOffset']['start'] < bounds['end']]
    bindings = [m for m in local if m['ruleId'] == 'binding'
                and variable in re.findall(r'\b\w+\b', m['metaVariables']['single']['BOUND']['text'])]
    if not bindings:
        return None
    unresolved = {'target': None, 'supporting': [], 'proof': [],
                  'boundary': 'Receiver has reassignment, shadowing or an unsupported initializer/type'}
    shadows = [m for m in local if m['ruleId'] in ('parameter', 'declared-name')
               and variable in re.findall(r'\b\w+\b', m['text'].split('=', 1)[0])]
    if len(bindings) != 1 or shadows:
        return unresolved
    binding = bindings[0]
    initializers = [m for m in local if m['ruleId'] == 'constructor-binding'
                    and m['range']['byteOffset'] == binding['range']['byteOffset']
                    and m['metaVariables']['single']['VARIABLE']['text'] == variable]
    if len(initializers) != 1 or binding['range']['byteOffset']['end'] > call['range']['byteOffset']['start']:
        return unresolved
    containing = [m for m in local if m['ruleId'] == 'block'
                  and m['range']['byteOffset']['start'] <= binding['range']['byteOffset']['start']
                  and binding['range']['byteOffset']['end'] <= m['range']['byteOffset']['end']]
    closest = min(containing, key=lambda m: m['range']['byteOffset']['end'] - m['range']['byteOffset']['start']) if containing else None
    if closest is None or closest['range']['byteOffset'] != body['range']['byteOffset']:
        return unresolved  # Nested/conditional declarations need scope and flow analysis.
    constructor_expression = initializers[0]['metaVariables']['single']['CONSTRUCTOR']['text']
    constructor, reason = resolve_direct_call(evidence, path, function, constructor_expression)
    if constructor is None:
        return {**unresolved, 'boundary': f'Constructor unresolved: {reason}'}
    constructor_path = constructor['path']
    definition = find_function(evidence, constructor_path, constructor['function'], None)
    results = [m for m in evidence.scan_go(constructor_path) if m['ruleId'] == 'function-result'
               and m['range']['byteOffset'] == definition['range']['byteOffset']]
    if len(results) != 1:
        return unresolved
    named = re.fullmatch(r'\s*\*?\s*(\w+)\s*', results[0]['metaVariables']['single']['RESULT']['text'])
    if named is None:
        return {**unresolved, 'boundary': 'Constructor result is not one ordinary local named type'}
    type_name = named[1]
    structs, methods = [], []
    for file in package_files(evidence, constructor_path):
        for match in evidence.scan_go(file):
            if match['ruleId'] == 'struct-type' and match['metaVariables']['single']['NAME']['text'] == type_name:
                structs.append((file, match))
            if match['ruleId'] != 'definition':
                continue
            variables = match['metaVariables']['single']
            receiver = variables.get('RECEIVER', {}).get('text', '')
            parsed = re.fullmatch(r'\(\s*\w+\s+\*?(\w+)\s*\)', receiver)
            if variables['NAME']['text'] == method_name and parsed is not None and parsed[1] == type_name:
                methods.append({'path': file, 'function': method_name, 'receiver': type_name})
    if len(structs) != 1 or len(methods) != 1:
        return {**unresolved, 'boundary': 'No unique concrete struct and method in the constructor package'}
    if 'BODY' not in definition['metaVariables']['single']:
        return {**unresolved, 'boundary': 'Constructor body unavailable'}
    return {'target': methods[0], 'supporting': [constructor], 'boundary': None,
            'proof': [{'path': file, 'start_line': match['range']['start']['line'] + 1,
                       'end_line': match['range']['end']['line'] + 1, 'role': role}
                      for file, match, role in [(path, binding, 'receiver initialization'),
                                               (constructor_path, definition, 'constructor and declared return type'),
                                               (*structs[0], 'concrete receiver declaration')]]}


def requested_resources(bundle: dict, requests: list[str], resources: list[dict]) -> tuple[list[dict], list[str]]:
    """Accept only dependency IDs from this step; arbitrary source paths cannot enter."""
    available = bundle['observations']['dependencies']
    additions, gaps = [], []
    for request in requests:
        if request not in available:
            raise ValueError(f'Unknown dependency ID: {request}')
        dependency = available[request]
        target = dependency['target']
        if target is None:
            gaps.append(f"{request} ({dependency['expression']}): {dependency['boundary']}")
        else:
            for resource in [*dependency.get('supporting', []), target]:
                if resource not in resources and resource not in additions:
                    additions.append(resource)
    return additions, gaps
