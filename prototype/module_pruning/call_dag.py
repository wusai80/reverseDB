"""Source-bound call graph projection and SCC condensation, not a deletion proof."""
from collections import deque


def condense(graph):
    """Condense all possible edges. Reachability means may be needed, not must execute."""
    nodes = {n['id']: n for n in graph['nodes']}
    if len(nodes) != len(graph['nodes']):
        raise ValueError('duplicate node')
    adjacency = {n: set() for n in nodes}
    for edge in graph['edges']:
        if edge['from'] not in nodes or edge['to'] not in nodes:
            raise ValueError('unknown edge endpoint')
        adjacency[edge['from']].add(edge['to'])
    if not set(graph['roots']) <= nodes.keys():
        raise ValueError('unknown root')
    # Iterative Kosaraju: avoid recursion limits on long method chains.
    seen, finish = set(), []
    for start in sorted(nodes):
        stack = [(start, False)]
        while stack:
            node, done = stack.pop()
            if done:
                finish.append(node)
            elif node not in seen:
                seen.add(node)
                stack.append((node, True))
                stack.extend((v, False) for v in sorted(adjacency[node], reverse=True) if v not in seen)
    reverse = {n: set() for n in nodes}
    for src, targets in adjacency.items():
        for dst in targets:
            reverse[dst].add(src)
    groups, seen = [], set()
    for start in reversed(finish):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            group.append(node)
            for previous in sorted(reverse[node]):
                if previous not in seen:
                    seen.add(previous)
                    stack.append(previous)
        groups.append(sorted(group))
    groups.sort()
    membership = {n: i for i, group in enumerate(groups) for n in group}
    pairs = sorted({(membership[e['from']], membership[e['to']]) for e in graph['edges']
                    if membership[e['from']] != membership[e['to']]})
    dag_adj = {i: set() for i in range(len(groups))}
    indegree = {i: 0 for i in dag_adj}
    for src, dst in pairs:
        dag_adj[src].add(dst)
        indegree[dst] += 1
    queue = deque(sorted(i for i in indegree if indegree[i] == 0))
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for dst in sorted(dag_adj[node]):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(dst)
    if len(order) != len(groups):
        raise ValueError('condensation is cyclic')
    # Preserve one root-to-node explanation without duplicating every path.
    witnesses = {r: {'root': r} for r in sorted(set(graph['roots']))}
    queue = deque(witnesses)
    while queue:
        src = queue.popleft()
        for dst in sorted(adjacency[src]):
            if dst not in witnesses:
                witnesses[dst] = {'predecessor': src}
                queue.append(dst)
    return {
        'components': [{'id': i, 'members': group,
                        'cyclic': len(group) > 1 or any(n in adjacency[n] for n in group)}
                       for i, group in enumerate(groups)],
        'edges': [{'from': a, 'to': b} for a, b in pairs],
        'topological_order': order, 'membership': membership,
        'reachability_witnesses': witnesses,
        'outside_represented_paths': sorted(nodes.keys() - witnesses.keys()),
        'classification': {n: ('retain_possible_dependency' if n in witnesses else
                               'unknown_outside_incomplete_graph') for n in sorted(nodes)},
        'deletion_authorized': False,
    }


def from_extraction(data, source_files, roots):
    """Project existing Java facts. Exact file allowlist excludes analysis fixtures.

    New extraction records class-owned initialization and constructors. Legacy
    inputs use file buckets. Static initialization remains conservatively rooted.
    """
    source_files = set(source_files)
    nodes, edges = {}, []

    def add(symbol, kind='external_or_unextracted', **metadata):
        if symbol not in nodes:
            nodes[symbol] = {'id': symbol, 'kind': kind, **metadata}

    def edge(src, dst, kind, evidence):
        add(src)
        add(dst)
        edges.append({'from': src, 'to': dst, 'kind': kind, 'evidence': evidence})

    for method in data['methods']:
        if method['location']['file'] in source_files:
            add(method['symbol'], 'method', location=method['location'],
                source_available=method['source_available'], declared_throws=method['declared_throws'],
                constructor=method.get('constructor'), owner_type=method.get('owner_type'))
    for field in data['sql_fields']:
        if field['location']['file'] in source_files:
            add(field['symbol'], 'sql_field', location=field['location'], effect=field['effect'])
    if not set(roots) <= nodes.keys():
        raise ValueError('roots must be declared in the selected source inputs')
    all_roots = list(roots)
    if 'initializers' in data:
        for init in data['initializers']:
            if init['location']['file'] in source_files:
                add(init['symbol'], 'class_initializer', initialization=init)
                if init['static'] or not init['owner_identity_complete']:
                    all_roots.append(init['symbol'])
        for field in data['initialized_fields']:
            if field['location']['file'] in source_files:
                add(field['symbol'], 'initialized_field', location=field['location'])
                edge(field['initializer'], field['symbol'], 'initializes_field_definition', field)
        for method in data['methods']:
            if method['location']['file'] in source_files and method.get('constructor'):
                for suffix in ('#<clinit>', '#<instance-init>'):
                    edge(method['symbol'], method['owner_type'] + suffix,
                         'constructor_initialization_dependency', {'location': method['location'],
                         'scope': 'dependency only, not initializer order or execution count'})
    else:
        for path in sorted(source_files):
            symbol = 'initializer:' + path
            add(symbol, 'conservative_initializer_root', location={'file': path})
            all_roots.append(symbol)
    for call in data['calls']:
        if call['location']['file'] not in source_files:
            continue
        caller = call['caller']
        if caller == 'field-initializer':
            caller = 'initializer:' + call['location']['file']
        evidence = {k: call[k] for k in ('location', 'dispatch', 'result_use', 'effect_summary_complete')}
        if 'conditions' in call:
            evidence['conditions'] = call['conditions']
        edge(caller, call['target_symbol'], call.get('call_kind', 'declared_call'), evidence)
        for target in call.get('reflection', {}).get('targets', []):
            edge(caller, target, 'reflection_may_target', {
                **evidence, 'reflection': call['reflection'],
                'qualification': 'depends_on_archived_manual_reflection_bridge_contract'})
        for sql in call['sql_field_arguments']:
            edge(caller, sql, 'sql_definition_dependency', evidence)
        # An ignored result NEVER suppresses the call or its effect obligation.
        if not call['effect_summary_complete'] or call['dispatch'] == 'virtual_targets_not_closed':
            unknown = 'unknown_call:' + call['location']['file'] + ':' + str(call['location']['start'])
            add(unknown, 'unknown_call_effect_or_dispatch', location=call['location'])
            edge(caller, unknown, 'unclosed_call_obligation', evidence)
    for effect in data['effects']:
        if effect['location']['file'] in source_files:
            symbol = 'effect:' + effect['location']['file'] + ':' + str(effect['location']['start']) + ':' + effect['kind']
            add(symbol, 'effect_obligation', effect=effect)
            edge(effect['method'], symbol, 'effect_dependency', effect)
    return {'schema': 'source-call-graph/v2', 'roots': sorted(set(all_roots)),
            'nodes': sorted(nodes.values(), key=lambda n: n['id']), 'edges': edges,
            'dependency_summary_complete': False, 'automatic_deletion_authorized': False,
            'limitations': ['Method-level may-call graph, not a data/control slice.',
                'External bodies, virtual dispatch, callbacks and implicit exceptions are incomplete.',
                'Class initialization is conservative, not exact activation/order/count semantics; legacy inputs use file buckets.',
                'Named new/this/super targets are bound; local/anonymous construction and reference activation stay unknown.',
                'Unknown obligations may hide edges to apparently unreachable nodes; retain those nodes.',
                'Reflection targets rely on the archived manually bound bridge contract.']}
