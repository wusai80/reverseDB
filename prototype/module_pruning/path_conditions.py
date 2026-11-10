"""Refute conjunctions of source-bound necessary guards; never prove execution.

Only immutable-value Boolean/String locals are emitted by the Java extractor.
No data sampling, solver dependency, interprocedural substitution or model scores.
"""
from copy import deepcopy


def _values(values, kind):
    if not isinstance(values, list):
        raise ValueError('values must be a list')
    if kind == 'boolean':
        valid = all(type(v) is bool for v in values)
    elif kind == 'java.lang.String':
        valid = all(v is None or type(v) is str for v in values)
    else:
        raise ValueError('unsupported variable type')
    if not valid:
        raise ValueError('predicate type mismatch')
    return set(values)


def evaluate(conditions, allowed=None):
    """UNSAT is for this call site's necessary guards in the supplied context.

    A satisfiable abstraction is only not_refuted, never reachable/executed.
    Contexts bind declaration IDs, not identifier names or observed values.
    """
    allowed = allowed or {}
    if conditions is None:
        return {'status': 'unknown', 'reason': 'no_condition_summary', 'executed': None}
    atoms = conditions['atoms']
    types, domains, excluded, evidence = {}, {}, {}, []
    constant_false = False
    for atom in atoms:
        if 'constant' in atom:
            if type(atom['constant']) is not bool:
                raise ValueError('invalid constant')
            constant_false |= not atom['constant']
            evidence.append(atom)
            continue
        var = atom['variable']; key, kind = var['id'], var['type']
        if key in types and types[key] != kind:
            raise ValueError('inconsistent declaration types')
        if key not in types:
            types[key] = kind
            domains[key] = {False, True} if kind == 'boolean' else None
            excluded[key] = set()
            if key in allowed:
                context = _values(allowed[key], kind)
                if not context:
                    raise ValueError('empty capability domain is not an admissible contract')
                domains[key] = context
        values = _values(atom['values'], kind)
        if atom['operator'] == 'in':
            domains[key] = values if domains[key] is None else domains[key] & values
        elif atom['operator'] == 'not_in':
            excluded[key] |= values
        else:
            raise ValueError('unsupported predicate')
        evidence.append(atom)
    contradictions = [key for key, domain in domains.items()
                      if domain is not None and not domain - excluded[key]]
    if constant_false or contradictions:
        return {'status': 'excluded_by_necessary_guards', 'contradictory_variables': contradictions,
                'constant_false': constant_false, 'guard_evidence': evidence,
                'context': {k: allowed[k] for k in types if k in allowed}, 'executed': None,
                'scope': 'this represented call site under this context; not global module deletion'}
    return {'status': 'unknown' if conditions.get('unknowns') else 'not_refuted',
            'reason': 'unsupported_controls' if conditions.get('unknowns') else 'necessary_guards_satisfiable_only',
            'unknowns': conditions.get('unknowns', []), 'executed': None}


def analyze_graph(graph, allowed=None):
    """Retain all unknown edges and every source node; provide a diagnostic projection."""
    allowed = allowed or {}
    registry = {}
    for edge in graph['edges']:
        for atom in (edge.get('evidence', {}).get('conditions') or {}).get('atoms', []):
            if 'variable' in atom:
                var = atom['variable']
                if var['id'] in registry and registry[var['id']] != var['type']:
                    raise ValueError('inconsistent declaration types')
                registry[var['id']] = var['type']
    for key, values in allowed.items():
        if key not in registry:
            raise ValueError('capability binding not found in source declarations')
        if not _values(values, registry[key]):
            raise ValueError('empty capability domain')
    annotated, retained = [], []
    for index, edge in enumerate(graph['edges']):
        decision = evaluate(edge.get('evidence', {}).get('conditions'), allowed)
        annotated.append({'edge_index': index, 'from': edge['from'], 'to': edge['to'],
                          'kind': edge['kind'], **decision})
        if decision['status'] != 'excluded_by_necessary_guards':
            retained.append(deepcopy(edge))
    projection = deepcopy(graph)
    projection['edges'] = retained
    projection['automatic_deletion_authorized'] = False
    return {'schema': 'necessary-guard-analysis/v1', 'decisions': annotated,
            'projection': projection, 'context': allowed, 'deletion_authorized': False,
            'scope': 'call-site lexical necessary conditions, no cross-call path conjunction'}
