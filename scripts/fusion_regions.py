"""Conservative placement regions over trusted, ordered semantic units.

SQL effects are inferred only for the small grammar below. The NewOrder adapter
is an explicit source-bound semantic summary, NOT automatic Java loop recovery.
Regions preserve source order; this is not a reordering or equivalence prover.
"""
import re


IDENT = r'[a-z_][a-z_0-9]*'
VALUE = r'(?:\?|[0-9]+)'
PREDICATE = rf'{IDENT}\s*=\s*{VALUE}(?:\s+and\s+{IDENT}\s*=\s*{VALUE})*'
TYPES = {'int32', 'timestamp', 'json', 'ordered_staged_lines'}
SOURCE_SHA = '9dfc5ac0c2b323da4ea68e1e91d4045788faffcaef109fbe68fb91546d18feb2'
ADAPTER_SHA = '32a302f441e7a42a609b32ff836cdcbbacca8c5f3a225e43c04816d7b69cde99'


def sql_effect(query):
    """Table-level may-alias effects; never infer disjoint keys from sample data."""
    query = ' '.join(query.lower().split())
    columns = rf'(?:\*|{IDENT}(?:\s*,\s*{IDENT})*)'
    select = re.fullmatch(rf'select {columns} from ({IDENT}) where {PREDICATE}'
                          rf'(?: order by {IDENT}(?:\s*,\s*{IDENT})*)?( for update)?', query)
    update = re.fullmatch(rf'update ({IDENT}) set (.+) where {PREDICATE}', query)
    insert = re.fullmatch(rf'insert into ({IDENT})\s*\(({IDENT}(?:\s*,\s*{IDENT})*)\)'
                          rf' values\s*\((\s*{VALUE}(?:\s*,\s*{VALUE})*\s*)\)', query)
    result = dict(known=True, reads=[], writes=[], locks=[])
    if select:
        table = select[1]
        result.update(reads=[table], locks=[table] if select[2] else [])
    elif update and all(re.fullmatch(rf'\s*{IDENT}\s*=\s*(?:{VALUE}|{IDENT}(?:\s*[+-]\s*{VALUE})?)\s*', term)
                        for term in update[2].split(',')):
        table = update[1]
        result.update(reads=[table], writes=[table], locks=[table])
    elif insert and len(insert[2].split(',')) == len(insert[3].split(',')):
        # Constraint/FK effects are not inferred here. Global write order and
        # the frozen schema contract remain necessary.
        result.update(writes=[insert[1]], locks=[insert[1]])
    else:
        result.update(known=False, reason='unsupported_sql_effect')
    return result


def enrich(unit, supported_types=None):
    """Unknown SQL/body/type/effect units become placement barriers."""
    unit = dict(unit)
    effects = [sql_effect(q) for q in unit.get('queries', [])]
    reasons = []
    if unit.get('placement') != 'database': reasons.append('host_or_transaction_boundary')
    if not unit.get('body_known', False): reasons.append('unknown_body_effect')
    if not unit.get('transaction'): reasons.append('missing_transaction_scope')
    if any(not e['known'] for e in effects): reasons.append('unsupported_sql_effect')
    allowed = TYPES if supported_types is None else supported_types
    if any(t not in allowed for t in [*unit.get('uses', {}).values(), *unit.get('defines', {}).values()]):
        reasons.append('unsupported_value_representation')
    unit['effects'] = {kind: sorted({v for e in effects for v in e[kind]}) for kind in ('reads', 'writes', 'locks')}
    unit['eligible'] = not reasons
    unit['barriers'] = reasons
    return unit


def analyze(units, external_inputs, supported_types=None):
    """Maximal contiguous known DB regions, with conservative dependency edges.

    All semantic units and inputs are trusted frontend contracts. Region cuts
    do not authorize new value serializers or commit/savepoint movement.
    """
    nodes = [enrich(u, supported_types) for u in units]
    ids = [n['id'] for n in nodes]
    if len(set(ids)) != len(ids): raise ValueError('Duplicate semantic unit')
    available = dict(external_inputs)
    for node in nodes:
        if any(available.get(v) != t for v, t in node.get('uses', {}).items()):
            node['eligible'] = False
            node['barriers'].append('missing_or_mismatched_value_producer')
        for v, t in node.get('defines', {}).items():
            if v in available: raise ValueError('Frontend must use unique SSA value names: '+v)
            available[v] = t
    edges = []
    def edge(a, b, kind, resource):
        edges.append(dict(before=a['id'], after=b['id'], kind=kind, resource=resource,
                          obligation='end(before) < begin(after)'))
    for i, a in enumerate(nodes):
        if i+1 < len(nodes): edge(a, nodes[i+1], 'source_order', 'ordered semantic units')
        for b in nodes[i+1:]:
            for value in sorted(set(a.get('defines', {})) & set(b.get('uses', {}))):
                edge(a, b, 'value_flow', value)
            ea, eb = a['effects'], b['effects']
            for kind, left, right in [('read_after_write', 'writes', 'reads'),
                                      ('write_after_read', 'reads', 'writes'),
                                      ('write_after_write', 'writes', 'writes')]:
                for table in sorted(set(ea[left]) & set(eb[right])): edge(a, b, kind, table)
            if ea['writes'] and eb['writes']: edge(a, b, 'write_order', 'schema constraints may couple tables')
            if ea['locks'] and eb['locks']: edge(a, b, 'lock_order', 'retain acquisition order')
            if a.get('checks'): edge(a, b, 'control_guard', ','.join(a['checks']))
    regions, current = [], []
    def flush():
        nonlocal current
        if not current: return
        members = {n['id'] for n in current}
        defined = {v for n in current for v in n.get('defines', {})}
        live_in = {v: t for n in current for v, t in n.get('uses', {}).items() if v not in defined}
        needed = {v for n in nodes if n['id'] not in members for v in n.get('uses', {})}
        live_out = {v: t for n in current for v, t in n.get('defines', {}).items() if v in needed}
        regions.append(dict(nodes=[n['id'] for n in current], transaction=current[0]['transaction'],
                            live_in=live_in, live_out=live_out,
                            sql_sites=sum(len(n.get('queries', [])) for n in current),
                            status='candidate_under_frontend_and_schema_contract'))
        current = []
    for node in nodes:
        if not node['eligible']:
            flush()
        else:
            if current and current[-1]['transaction'] != node['transaction']: flush()
            current.append(node)
    flush()
    return dict(kind='ordered-fusion-regions/v1', nodes=nodes, edges=edges, regions=regions,
                barriers=[dict(node=n['id'], reasons=n['barriers']) for n in nodes if not n['eligible']],
                policy='Placement only; whole phases, source order, no operation/data deletion')


def schedule(nodes):
    return [dict(node=n, event=event) for n in nodes for event in ('begin', 'end')]


def validate_schedule(graph, events):
    """Begin/end delimit the ENTIRE phase, including every input-loop iteration."""
    ids = {n['id'] for n in graph['nodes']}
    positions, violations = {}, []
    for i, event in enumerate(events):
        key = (event.get('node'), event.get('event'))
        if key[0] not in ids or key[1] not in ('begin', 'end'):
            violations.append(dict(kind='unknown_schedule_event', event=event)); continue
        if key in positions: violations.append(dict(kind='duplicate_schedule_event', node=key[0]))
        positions[key] = i
    for node in ids:
        if any((node, e) not in positions for e in ('begin', 'end')):
            violations.append(dict(kind='missing_phase', node=node))
        elif positions[node, 'begin'] >= positions[node, 'end']:
            violations.append(dict(kind='invalid_phase_interval', node=node))
    for edge in graph['edges']:
        a, b = (edge['before'], 'end'), (edge['after'], 'begin')
        if a in positions and b in positions and positions[a] >= positions[b]: violations.append(edge)
    return dict(accepted=not violations, violations=violations)


def neworder_units(ir):
    """Manually reviewed semantic grouping over the existing extracted IR.

    Stage-lines is ONE whole-input phase. We do not infer per-key independence
    or automatically prove scalar/exception/loop semantics here.
    """
    if ir.get('source_sha256') != SOURCE_SHA or ir.get('adapter_sha256') != ADAPTER_SHA:
        raise ValueError('Unsupported frontend source contract')
    helpers = [('getCustomer', 'stmtGetCustSQL'), ('getWarehouse', 'stmtGetWhseSQL'),
               ('getDistrict', 'stmtGetDistSQL'), ('updateDistrict', 'stmtUpdateDistSQL'),
               ('insertOpenOrder', 'stmtInsertOOrderSQL'), ('insertNewOrder', 'stmtInsertNewOrderSQL')]
    if ir['helper_order'] != [h for h, _ in helpers]+['getItemPrice', 'getStock', 'getDistInfo']:
        raise ValueError('Unsupported helper order')
    if ir['batch_order'] != ['stmtInsertOrderLine', 'stmtUpdateStock']:
        raise ValueError('Unsupported batch order')
    external = dict(warehouse='int32', district='int32', customer='int32', lines='json', time='timestamp')
    units = [dict(id='input_validation', placement='host', body_known=True, uses=external,
                  defines={}, transaction=None, queries=[])]
    def add(id, fields=(), queries=(), uses=None, defines=None, checks=(), phase='once'):
        units.append(dict(id=id, placement='database', body_known=True, transaction='outer_serializable',
                          queries=[ir['sql'][f]['sql'] for f in fields]+list(queries),
                          source=[ir['sql'][f]['source'] for f in fields],
                          uses=uses or {}, defines=defines or {}, checks=list(checks), phase=phase,
                          lowering_contract='pinned-NewOrder-backend/v1'))
    add('response_id', queries=[ir['adapter_queries'][0]], uses={'district':'int32'},
        defines={'response_id':'int32'}, checks=['initial district exists'])
    for helper, field in helpers:
        uses = {'warehouse':'int32', 'district':'int32'}
        if helper in ('getCustomer', 'insertOpenOrder'): uses['customer']='int32'
        if helper in ('insertOpenOrder', 'insertNewOrder'): uses['order_id']='int32'
        if helper == 'insertOpenOrder': uses.update(lines='json', time='timestamp')
        add(helper, [field], uses=uses, defines={'order_id':'int32'} if helper=='getDistrict' else {},
            checks=[helper+' business check'] if helper in ('getCustomer','getWarehouse','getDistrict','updateDistrict') else [])
    add('stage_lines', ['stmtGetItemSQL','stmtGetStockSQL'],
        uses=dict(warehouse='int32',district='int32',lines='json'), defines={'staged_lines':'ordered_staged_lines'},
        checks=['item exists','stock exists'], phase='all_input_lines_in_source_order')
    add('flush_lines', ['stmtInsertOrderLineSQL'],
        uses=dict(warehouse='int32',district='int32',order_id='int32',staged_lines='ordered_staged_lines'),
        phase='all_input_lines_in_source_order')
    add('flush_stock', ['stmtUpdateStockSQL'], uses=dict(warehouse='int32',staged_lines='ordered_staged_lines'),
        phase='all_input_lines_in_source_order')
    add('result_order', queries=[ir['adapter_queries'][1]], uses=dict(district='int32',response_id='int32'), defines={'order_json':'json'})
    add('result_lines', queries=[ir['adapter_queries'][2]], uses=dict(district='int32',response_id='int32'), defines={'lines_json':'json'})
    units.append(dict(id='commit_or_rollback_and_return', placement='host', body_known=True, transaction=None,
                      queries=[], uses=dict(order_json='json',lines_json='json'), defines={}))
    return units, external


def plan_neworder(ir):
    units, external = neworder_units(ir)
    graph = analyze(units, external)
    graph['source_contract'] = dict(source_sha256=ir['source_sha256'], adapter_sha256=ir['adapter_sha256'],
                                    summary='Manual loop/value/exception summaries; extracted SQL and source order')
    return graph


def require_neworder_region(ir):
    graph = plan_neworder(ir)
    expected = [n['id'] for n in graph['nodes'][1:-1]]
    if len(graph['regions']) != 1 or graph['regions'][0]['nodes'] != expected:
        raise ValueError('NewOrder backend requires one complete admitted region: '+str(graph['barriers']))
    return graph
