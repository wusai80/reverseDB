"""One direct call boundary: substitute stable actuals into callee guards.

Results describe caller-site/callee-site pairs, never globally remove a method.
No recursive propagation, dynamic dispatch, heap state, or return-value summary.
"""
from collections import defaultdict
from copy import deepcopy

from call_dag import condense
from path_conditions import evaluate


def site_id(call):
    loc = call['location']
    return loc['file'] + ':' + str(loc['start'])


def deferred(conditions):
    return any(u.get('reason') == 'deferred_boundary_outer_conditions_not_inherited'
               for u in (conditions or {}).get('unknowns', []))


def compose(outer, inner):
    """Compose a single invocation context; unknown transfers never justify exclusion."""
    transfer = outer.get('argument_transfer', {})
    left, right = outer.get('conditions'), inner.get('conditions')
    reason = None
    if transfer.get('status') != 'direct_source_target':
        reason = transfer.get('reason', 'no_direct_argument_binding')
    elif transfer.get('target') != outer['target_symbol'] or inner['caller'] != outer['target_symbol']:
        reason = 'target_binding_mismatch'
    elif left is None or right is None:
        reason = 'missing_condition_summary'
    elif deferred(left) or deferred(right):
        reason = 'deferred_invocation_context'
    if reason:
        return {'status': 'unknown', 'reason': reason, 'executed': None}
    bindings, mapping = transfer['bindings'], {}
    for binding in bindings:
        formal, actual = binding.get('formal'), binding['actual']
        if formal is None:
            # Unsupported formals may have data effects; do not invent a value.
            continue
        if formal['id'] in mapping:
            return {'status': 'unknown', 'reason': 'duplicate_formal_binding', 'executed': None}
        if actual['kind'] == 'stable_variable' and actual['variable']['type'] == formal['type']:
            mapping[formal['id']] = actual
        elif actual['kind'] == 'literal' and actual.get('type') == formal['type']:
            mapping[formal['id']] = actual
        else:
            return {'status': 'unknown', 'reason': 'unsupported_actual_value', 'executed': None}
    atoms = deepcopy(left['atoms'])
    for original in right['atoms']:
        atom = deepcopy(original)
        if 'variable' in atom:
            replacement = mapping.get(atom['variable']['id'])
            if replacement and replacement['kind'] == 'stable_variable':
                atom['variable'] = deepcopy(replacement['variable'])
            elif replacement and replacement['kind'] == 'literal':
                if atom['operator'] not in ('in', 'not_in'):
                    raise ValueError('unsupported callee predicate')
                contains = replacement['value'] in atom['values']
                atom = {'constant': contains if atom['operator'] == 'in' else not contains,
                        'substituted_from': original, 'literal': replacement['value']}
            else:
                # Callee locals get a fresh call frame; never equate them by name.
                atom['variable']['id'] = 'frame:' + site_id(outer) + ':' + atom['variable']['id']
        atoms.append(atom)
    combined = {'atoms': atoms, 'unknowns': deepcopy(left['unknowns'] + right['unknowns']),
                'full_path_condition_complete': False}
    decision = evaluate(combined)
    return {**decision, 'combined_conditions': combined, 'bindings': deepcopy(bindings),
            'scope': 'one explicit direct invocation and one nested call site only'}


def reflection_calls(data, calls):
    """Add conditional target-entry views; never replace the fallible bridge call."""
    expanded, rejected = [], []
    contract = data.get('reflection_bridge', {})
    for call in calls:
        if 'reflection' not in call:
            continue
        proof = call.get('reflection_argument_transfer', {})
        resolution = call['reflection']
        reason = None
        if proof.get('status') != 'verified_bridge_target':
            reason = proof.get('reason', 'no_verified_bridge_mapping')
        elif (proof.get('bridge_symbol') != contract.get('symbol') or
              proof.get('bridge_method_sha256') != contract.get('method_sha256') or
              not contract.get('method_sha256') or call['target_symbol'] != contract.get('symbol')):
            reason = 'bridge_contract_mismatch'
        elif (resolution.get('targets') != [proof.get('target')] or
              len(resolution.get('receiver_types', [])) != 1 or
              resolution.get('ambiguous_receiver_types') or resolution.get('no_matching_method')):
            reason = 'target_not_unique'
        elif (type(proof.get('parameter_count')) is not int or proof['parameter_count'] < 0 or
              len(proof.get('bindings', [])) != proof['parameter_count']):
            reason = 'incomplete_parameter_certificate'
        elif any(b.get('position') != i or b.get('bridge_argument_index') != i+2 or b.get('type_compatible') is not True
                 for i,b in enumerate(proof.get('bindings', []))):
            reason = 'position_or_type_certificate_invalid'
        if reason:
            rejected.append({'outer_site': site_id(call), 'status':'unknown', 'reason':reason})
            continue
        view = deepcopy(call)
        view['target_symbol'] = proof['target']
        view['argument_transfer'] = {'status':'direct_source_target', 'target':proof['target'],
                                     'bindings':deepcopy(proof['bindings'])}
        view['via'] = 'verified_reflection_bridge'
        view['target_body_available'] = proof.get('body_available') is True
        view['bridge_obligation'] = deepcopy(proof)
        expanded.append(view)
    return expanded, rejected


def analyze(data, source_files, include_reflection=False):
    source_files = set(source_files)
    methods = {m['symbol']: m for m in data['methods'] if m['location']['file'] in source_files}
    calls = [c for c in data['calls'] if c['location']['file'] in source_files]
    rejected = []
    if include_reflection:
        expanded, rejected = reflection_calls(data, calls)
        calls += expanded
    by_caller = defaultdict(list)
    for call in calls:
        by_caller[call['caller']].append(call)
    recursion_graph = {'nodes': [{'id': m} for m in methods], 'roots': [], 'edges': [
        {'from': c['caller'], 'to': c['target_symbol']} for c in calls
        if c['caller'] in methods and c['target_symbol'] in methods]}
    scc = condense(recursion_graph)
    recursive = {m for group in scc['components'] if group['cyclic'] for m in group['members']}
    records, boundaries = [], []
    for outer in calls:
        target = outer['target_symbol']
        if target not in methods or (outer.get('via') == 'verified_reflection_bridge' and not outer.get('target_body_available')):
            boundaries.append({'outer_site': site_id(outer), 'caller': outer['caller'],
                               'target': target, 'status': 'unknown', 'reason': 'target_body_outside_selected_sources',
                               'via': outer.get('via','direct_or_original_bridge')})
            continue
        for inner in by_caller[target]:
            if outer['caller'] in recursive or target in recursive:
                decision = {'status': 'unknown', 'reason': 'recursive_context_not_supported', 'executed': None}
            else:
                decision = compose(outer, inner)
            records.append({'outer_site': site_id(outer), 'inner_site': site_id(inner),
                'caller': outer['caller'], 'callee': target, 'inner_target': inner['target_symbol'],
                'outer_location': outer['location'], 'inner_location': inner['location'],
                'via': outer.get('via','direct_or_original_bridge'),
                'inner_via': inner.get('via','direct_or_original_bridge'),
                'bridge_invocation_may_fail': True if outer.get('via') else None, **decision})
    return {'schema': 'one-hop-call-conditions/v1', 'records': records, 'boundaries': boundaries,
            'recursive_methods': sorted(recursive), 'deletion_authorized': False,
            'reflection_enabled': include_reflection, 'reflection_rejections': rejected,
            'global_edges_removed': 0, 'dynamic_execution_observed': False,
            'limitations': ['Context pairs only; no global deletion or cross-request assumption.',
                'Direct targets plus opt-in hash-bound unique reflection bridge target-entry views.',
                'Reflection entry mapping does not remove bridge failures; external target bodies remain unknown.',
                'Recursion and deferred invocation stay unknown; no return, heap or full-effect summary.',
                'Not refuted is not proof of executable paths.']}
