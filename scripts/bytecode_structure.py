"""Open-world structural graph, never a reachability-based deletion classifier."""
from collections import Counter, defaultdict


def index_classes(records):
    classes, duplicates = {}, []
    for c in records:
        if c['class'] in classes:
            duplicates.append(dict(name=c['class'], selected=classes[c['class']]['source'],
                                   shadowed=c['source'], same_bytes=classes[c['class']]['sha256']==c['sha256']))
        else:
            classes[c['class']] = c
    return classes, duplicates


def components(graph):
    """Iterative Kosaraju: collapse recursion, not a claim the call graph is a DAG."""
    visited, order = set(), []
    for root in sorted(graph):
        if root in visited: continue
        stack = [(root, False)]
        while stack:
            node, done = stack.pop()
            if done: order.append(node); continue
            if node in visited: continue
            visited.add(node); stack.append((node, True))
            stack.extend((n, False) for n in sorted(graph[node], reverse=True) if n not in visited)
    reverse = {n:set() for n in graph}
    for n, targets in graph.items():
        for target in targets: reverse[target].add(n)
    assigned, groups = {}, []
    for root in reversed(order):
        if root in assigned: continue
        group, stack = [], [root]
        while stack:
            n = stack.pop()
            if n in assigned: continue
            assigned[n] = len(groups); group.append(n); stack.extend(sorted(reverse[n]))
        groups.append(sorted(group))
    edges = sorted({(assigned[a], assigned[b]) for a, bs in graph.items() for b in bs if assigned[a]!=assigned[b]})
    return dict(components=groups, edges=edges,
                cyclic_components=[i for i,g in enumerate(groups) if len(g)>1 or g[0] in graph[g[0]]])


def analyze(classes, roots):
    methods = {m['id']:m for c in classes.values() for m in c['methods']}
    app = {m['id']:m for c in classes.values() if c['application'] for m in c['methods']}
    assert roots and all(r in app for r in roots)
    parents = {n: [x for x in [c['super'], *c['interfaces']] if x] for n,c in classes.items()}
    ancestor_cache = {}
    def ancestors(owner):
        if owner not in ancestor_cache:
            seen, stack = set(), [owner]
            while stack:
                n = stack.pop()
                if n in seen: continue
                seen.add(n); stack.extend(parents.get(n, []))
            ancestor_cache[owner] = seen
        return ancestor_cache[owner]
    overrides = defaultdict(set)
    for owner,c in classes.items():
        if not c['application']: continue
        lineage = ancestors(owner)
        for base in sorted(lineage):
            for m in classes.get(base, {}).get('methods', []):
                mid=m['id']; signature=mid.split('#',1)[1]
                if signature.startswith('<') or m['access'] & (0x0002|0x0008): continue
                # Include inherited implementations of interfaces added by a subclass.
                for parent in lineage: overrides[(parent,signature)].add(mid)
    def resolve(target):
        if target in methods: return [target]
        owner, signature = target.split('#', 1)
        if signature.startswith('<'): return []
        # Conservatively include inherited declarations; no most-specific dispatch claim.
        return sorted(n+'#'+signature for n in ancestors(owner) if n+'#'+signature in methods)
    graph, edges, boundaries, pending = {}, [], [], list(reversed(roots))
    while pending:
        caller = pending.pop()
        if caller in graph: continue
        graph[caller] = set()
        method = app[caller]
        if method['access'] & (0x0100|0x0400):
            boundaries.append(dict(caller=caller, kind='abstract_or_native_body', target=caller))
        if method['annotations']:
            boundaries.append(dict(caller=caller, kind='annotation_effects_not_resolved', target=caller))
        for call in method['calls']:
            if call['kind']=='invoke':
                targets=[(call['target'], 'declared_invocation', call['opcode'] in (182,185))]
            else:
                boundaries.append(dict(caller=caller, index=call['index'], kind='dynamic_bootstrap', target=call['bootstrap']['target']))
                # Method handles denote possible deferred callbacks, not evidence of execution.
                targets=[(h['target'], 'possible_handle_callback', h['tag'] in (5,9)) for h in call['handles'] if h['tag']>=5]
            for target, kind, virtual in targets:
                candidates = set(resolve(target))
                if virtual:
                    owner,signature = target.split('#',1)
                    candidates.update(overrides.get((owner,signature),set()))
                    boundaries.append(dict(caller=caller,index=call['index'],kind='open_virtual_dispatch',target=target))
                if not candidates:
                    boundaries.append(dict(caller=caller,index=call['index'],kind='unresolved_declaration',target=target))
                for candidate in sorted(candidates):
                    edges.append(dict(caller=caller,index=call['index'],declared_target=target,target=candidate,
                                      kind=kind, virtual=virtual, application_target=candidate in app))
                    if candidate in app:
                        graph[caller].add(candidate); pending.append(candidate)
                    else:
                        boundaries.append(dict(caller=caller,index=call['index'],kind='dependency_body_not_indexed',target=candidate))
                if target.startswith(('java/lang/reflect/','java/lang/invoke/','java/lang/Class#forName',
                                      'java/util/ServiceLoader#','java/lang/ClassLoader#')):
                    boundaries.append(dict(caller=caller,index=call['index'],kind='reflection_or_runtime_linkage',target=target))
        if method['fields'] or method['types'] or any(c.get('opcode')==184 for c in method['calls']):
            boundaries.append(dict(caller=caller,kind='implicit_initialization_and_type_effects_not_expanded',target=caller))
    dag = components(graph)
    return dict(roots=roots, methods_in_structural_envelope=sorted(graph), edges=edges, boundaries=boundaries, condensation=dag,
                summary=dict(application_classes=sum(c['application'] for c in classes.values()),application_methods=len(app),
                             indexed_classes=len(classes),indexed_methods=len(methods),envelope_methods=len(graph),
                             envelope_classes=len({m.split('#')[0] for m in graph}),edges=len(edges),
                             boundaries=dict(Counter(b['kind'] for b in boundaries)),
                             components=len(dag['components']),cyclic_components=len(dag['cyclic_components'])),
                policy=dict(deletion_authorized=False, dynamic_execution_proven=False, whole_program_closed=False,
                    outside_envelope='unclassified; absence of an edge or runtime witness never authorizes deletion',
                    dependencies='all bundled runtime type/method headers; bodies remain explicit boundaries',
                    dispatch='application hierarchy candidates only; open-world proxies, callbacks and generated classes remain unknown',
                    effects='branches, handlers, field/type references and annotations are facts, not semantic dependence proofs',
                    dag='SCC condensation of an incomplete potential-call graph, not an execution or complete dependence DAG'))
