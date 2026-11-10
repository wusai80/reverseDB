"""Apply the original selector within source-ordered, cost-complete islands.

This is an input adapter, not a second optimizer. Unknown units remain barriers;
the call graph's topological order is never used as program execution order.
"""
import copy
from placement_frontend import select_complete_contract


def bounded_cost(node, parameters):
    cost = node.get('java_calls')
    if not isinstance(cost, dict): return False
    if type(cost.get('base', 0)) is not int or cost.get('base', 0) < 0: return False
    terms = cost.get('terms', {})
    if not isinstance(terms, dict): return False
    for name, coefficient in terms.items():
        bounds = parameters.get(name)
        if type(coefficient) is not int or coefficient < 0: return False
        if not (isinstance(bounds, (tuple, list)) and len(bounds) == 2 and
                all(type(v) is int for v in bounds) and 0 <= bounds[0] <= bounds[1]): return False
    return True


def validate_source_structure(contract):
    """Validate SSA and source order without assigning a cost to unknown units."""
    nodes = contract['nodes']; names = [n['id'] for n in nodes]
    if len(names) != len(set(names)): raise ValueError('Duplicate unit identity')
    available = dict(contract['external_inputs'])
    for n in nodes:
        for name, type_ in n.get('uses', {}).items():
            if available.get(name) != type_: raise ValueError('Unresolved source value: ' + name)
        for name, type_ in n.get('defines', {}).items():
            if name in available: raise ValueError('Duplicate source value: ' + name)
            available[name] = type_
    positions = {name:i for i,name in enumerate(names)}
    for e in contract['edges']:
        if e['before'] not in positions or e['after'] not in positions or positions[e['before']] >= positions[e['after']]:
            raise ValueError('Edge contradicts source order')


def bind_summary(contract, binding, classes):
    """Bind a supplied semantic summary to bytes; hashes do not prove semantics."""
    method = binding['method']; owner = method.split('#', 1)[0]
    if owner not in binding['class_sha256']: raise ValueError('Missing anchor class hash')
    for name, expected in binding['class_sha256'].items():
        if name not in classes or classes[name]['sha256'] != expected:
            raise ValueError('Changed or absent source class: ' + name)
    if method not in {m['id'] for m in classes[owner]['methods']}:
        raise ValueError('Missing exact method descriptor')
    declared = contract.get('scope', {}).get('method') or contract.get('source', {}).get('method')
    if declared != method: raise ValueError('Semantic summary belongs to another method')
    return dict(method=method, binding=copy.deepcopy(binding),
                scope=copy.deepcopy(contract.get('scope', {})),
                authority='existing frozen semantic summary; indexed bytes matched; conditions not generalized')


def select_partial_contract(contract):
    validate_source_structure(contract)
    complete = select_complete_contract(contract)
    if complete['status'] == 'retention_conflict_no_plan':
        return dict(status='retention_conflict_no_partial_plan', whole=complete, islands=[], barriers=[], global_cost=None)
    if complete['selection'] is not None:
        return dict(status='whole_contract_selected', whole=complete, islands=[], barriers=[],
                    global_cost=copy.deepcopy(complete['selection']['selected_calls']))
    nodes = contract['nodes']; domains = contract.get('parameters', {})
    known = [bounded_cost(n, domains) for n in nodes]
    # Invalid numeric costs are rejected, never repaired or interpreted as zero.
    for n, ok in zip(nodes, known):
        if n.get('java_calls') is not None:
            cost = n['java_calls']
            numeric = dict(n, java_calls=dict(cost, terms={}))
            if not bounded_cost(numeric, {}): raise ValueError('Invalid numeric cost')
            if any(type(v) is not int or v < 0 for v in cost.get('terms', {}).values()): raise ValueError('Invalid coefficient')
    islands, barriers = [], []
    cursor = 0
    while cursor < len(nodes):
        if not known[cursor]:
            n = nodes[cursor]
            barriers.append(dict(index=cursor, node=n['id'], reason='unknown cost' if n.get('java_calls') is None else 'unbounded parameter domain',
                                 action='retain complete original unit and effects; no crossing or deletion'))
            cursor += 1; continue
        start = cursor
        while cursor < len(nodes) and known[cursor]: cursor += 1
        stop = cursor; part = copy.deepcopy(nodes[start:stop]); ids = {n['id'] for n in part}
        local_definitions = {v for n in part for v in n.get('defines', {})}
        inputs = {v:t for n in part for v,t in n.get('uses', {}).items() if v not in local_definitions}
        later_uses = {v for n in nodes[stop:] for v in n.get('uses', {})}
        outputs = {v:t for n in part for v,t in n.get('defines', {}).items() if v in later_uses}
        parameters = {v for n in part for v in n['java_calls'].get('terms', {})}
        edges = [copy.deepcopy(e) for e in contract['edges'] if e['before'] in ids and e['after'] in ids]
        crossing = [copy.deepcopy(e) for e in contract['edges'] if (e['before'] in ids) != (e['after'] in ids)]
        boundary = None
        if outputs:
            boundary = '__retained_suffix_interface'
            while boundary in {n['id'] for n in nodes}: boundary += '_'
            part.append(dict(id=boundary, body_known=False, transaction=None, uses=outputs, defines={}, queries=[],
                             java_calls=dict(base=0, terms={}), database=None,
                             requires=['original_suffix_receives_all_live_values'],
                             host_retention=dict(required=True, reason='Analysis-only live-out consumer; never executable or movable'),
                             analysis_only=True,
                             retention_reason='ABI marker, not the suffix computation or a zero-cost claim about unknown code'))
            edges.append(dict(before=nodes[stop-1]['id'], after=boundary, kind='retained_boundary', resource='all_live_out_values'))
        local = dict(nodes=part, edges=edges, external_inputs=inputs,
                     parameters={v:copy.deepcopy(domains[v]) for v in sorted(parameters)},
                     wire_types=copy.deepcopy(contract['wire_types']),max_boundary_values=contract.get('max_boundary_values'),
                     contract_authority=contract.get('contract_authority', 'inherited frozen semantic summary'),
                     scope=dict(parent_scope=copy.deepcopy(contract.get('scope', {})),
                                interval=[start,stop], boundary_marker='analysis-only; excluded from runtime and parent cost'))
        result = select_complete_contract(local)
        assert result['selection'] is not None
        for region in result['selection']['database_regions']:
            assert boundary not in region['nodes']
        islands.append(dict(start=start, stop=stop, original_nodes=[n['id'] for n in nodes[start:stop]],
                            live_in=inputs, live_out=outputs, retained_cross_boundary_edges=crossing,
                            contract=local, result=result))
    return dict(status='partial_local_selection_only',whole=complete,islands=islands,barriers=barriers,global_cost=None,
                limitations=['no global objective or executable whole-program plan',
                             'parent/child summaries are alternatives, never summed or flattened together',
                             'unknown units remain at original positions; all failure/control boundaries retained',
                             'local result inherits all original scope, input and transaction assumptions'])
