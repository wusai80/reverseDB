"""Lossless common contract description; no new optimizer or semantic prover.

An existing DP binding can be replayed only if its complete archived input is
unchanged. Unbound descriptions produce no plan, not a fictitious retained plan.
"""
import copy
import hashlib
import json
from pathlib import Path

from partial_placement import bounded_cost, validate_source_structure
from placement_frontend import select_complete_contract

SCHEMA = 'source-bound-region-description/v1'
FIELDS = {'uses': 'inputs', 'defines': 'outputs', 'java_calls': 'original_calls',
          'database': 'backend_implementation'}


def normalize(program):
    """Rename historical Java labels; retain every other field, including unknowns."""
    result = copy.deepcopy(program)
    units = []
    for original in result.pop('nodes'):
        unit = dict(original)
        for old, new in FIELDS.items():
            if new in unit:
                raise ValueError('Conflicting common field: ' + new)
            if old in unit:
                unit[new] = unit.pop(old)
        units.append(unit)
    if 'units' in result:
        raise ValueError('Conflicting unit container')
    result['units'] = units
    return result


def denormalize(description):
    result = copy.deepcopy(description)
    nodes = []
    for common in result.pop('units'):
        node = dict(common)
        for old, new in FIELDS.items():
            if old in node:
                raise ValueError('Conflicting legacy field: ' + old)
            if new in node:
                node[old] = node.pop(new)
        nodes.append(node)
    if 'nodes' in result:
        raise ValueError('Conflicting node container')
    result['nodes'] = nodes
    return result


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def describe(program, *, scope, provenance, evidence, unresolved, dp_binding=None):
    """All facts/scope are supplied frontend assertions; never inferred as proofs."""
    validate_source_structure(program)
    if not scope.get('entry') or not scope.get('execution_domain') or not scope.get('observables'):
        raise ValueError('Missing explicit interface or execution scope')
    if scope.get('interface_inputs') != program['external_inputs']:
        raise ValueError('Interface inputs differ from source inputs')
    outputs = scope.get('interface_outputs')
    available = dict(program['external_inputs'])
    for unit in program['nodes']:
        available.update(unit.get('defines', {}))
    if not isinstance(outputs, dict) or any(available.get(v) != t for v, t in outputs.items()):
        raise ValueError('Missing or unbound interface output')
    common = normalize(program)
    if denormalize(common) != program:
        raise ValueError('Non-lossless representation')
    return dict(schema=SCHEMA, scope=copy.deepcopy(scope), program=common,
                provenance=copy.deepcopy(provenance), evidence=copy.deepcopy(evidence),
                unresolved=copy.deepcopy(unresolved), dp_binding=copy.deepcopy(dp_binding),
                authority='Source-bound frontend assertions and bounded evidence; not machine-proved equivalence')


def select_existing_binding(description, root):
    """Invoke the original selector only through an exact existing input binding.

    This is an integrity/replay boundary, not a way to admit new lowerings by
    declaring their proof obligations satisfied. No project names are inspected.
    """
    if description.get('schema') != SCHEMA:
        raise ValueError('Unsupported description schema')
    program = denormalize(description['program'])
    validate_source_structure(program)
    for record in [*description['provenance'], *description['evidence']]:
        if fingerprint(Path(root) / record['path']) != record['sha256']:
            raise ValueError('Changed provenance/evidence: ' + record['path'])
    binding = description.get('dp_binding')
    if binding is None:
        return dict(status='unbound_description_no_plan', selection=None,
                    unresolved=copy.deepcopy(description['unresolved']),
                    reason='Description is not an admitted backend contract; no optimizer invocation')
    if description['unresolved']:
        return dict(status='unresolved_binding_no_plan', selection=None,
                    unresolved=copy.deepcopy(description['unresolved']))
    path = Path(root) / binding['path']
    if fingerprint(path) != binding['sha256']:
        raise ValueError('Changed archived DP binding')
    if json.loads(path.read_text()) != program:
        raise ValueError('Description differs from bound DP input')
    # Scope text is annotation of inherited authority, never new authorization.
    return select_complete_contract(program)


def inspect_description_evidence(description, root):
    """Join access observations to the SAME old interval checker, never promote them.

    Returned interval checks are diagnostics, not an executable new plan.
    Explicit interface outputs get an analysis-only consumer so that the old
    checker's liveness calculation cannot silently lose terminal return values.
    The actual old selector still receives its exact original bound contract.
    """
    from fusion_placement import check_contract, inspect_interval
    from sql_effect_observations import attach_observations

    if description.get('schema') != SCHEMA:
        raise ValueError('Unsupported description schema')
    program = denormalize(description['program'])
    # Recheck typed public interfaces too, not only node-local producers.
    describe(program, scope=description['scope'], provenance=description['provenance'],
             evidence=description['evidence'], unresolved=description['unresolved'],
             dp_binding=description.get('dp_binding'))
    for record in [*description['provenance'], *description['evidence']]:
        if fingerprint(Path(root)/record['path']) != record['sha256']:
            raise ValueError('Changed provenance/evidence: '+record['path'])
    sidecar = description.get('analysis_observations')
    if sidecar is not None:
        if sidecar.get('authorizes_replacement') is not False:
            raise ValueError('Access sidecar cannot grant admission')
        observations = sidecar['sql_access']
        attach_observations(description, observations)  # exact coverage/order/hash
    else:
        observations = {n['id']: [] for n in program['nodes']}

    unit_evidence, conflicts = [], []
    for unit in program['nodes']:
        rows = observations[unit['id']]
        accesses = [r['direct_access'] for r in rows if r.get('direct_access') is not None]
        observed = sorted({t for a in accesses for t in a['reads']})
        declared = unit.get('effects', {}).get('reads')
        additional = sorted(set(observed)-set(declared)) if declared is not None else []
        if additional:
            conflicts.append(dict(node=unit['id'], kind='additional_observed_read', tables=additional,
                                  action='Review source/schema/deployment summary; no plan while unresolved'))
        unit_evidence.append(dict(node=unit['id'], observed_direct_reads=observed,
            declared_reads=declared, observation_statuses=[r['status'] for r in rows],
            functions=sorted({f['name'] for r in rows for f in r.get('functions', [])}),
            named_obligations=copy.deepcopy(unit.get('requires', [])),
            original_body_known=unit.get('body_known', False),
            has_declared_backend=unit.get('database') is not None,
            discharges_semantic_obligations=False,
            interpretation='Supporting access facts only; absence of a recorded access/effect is not proof of absence'))

    if conflicts:
        decision = dict(status='evidence_conflict_no_plan', selection=None, conflicts=conflicts)
    else:
        decision = select_existing_binding(description, root)
    inspection = copy.deepcopy(program)
    source_count = len(inspection['nodes'])
    outputs = copy.deepcopy(description['scope']['interface_outputs'])
    marker = None
    if outputs:
        marker = '__external_observation_boundary'
        ids = {n['id'] for n in inspection['nodes']}
        while marker in ids:
            marker += '_'
        inspection['nodes'].append(dict(id=marker, uses=outputs, defines={}, queries=[],
            transaction=None, body_known=False, database=None, java_calls=dict(base=0, terms={}),
            analysis_only=True, retention_reason='External interface consumer, not executable work or a runtime cost claim'))
        if source_count:
            inspection['edges'].append(dict(before=inspection['nodes'][source_count-1]['id'], after=marker,
                                            kind='observation_boundary', resource='all declared returned values'))
    intervals = []
    incomplete = [n['id'] for n in program['nodes'] if not bounded_cost(n, program.get('parameters', {}))]
    if not incomplete:
        check_contract(inspection)
        for stop in range(1, source_count+1):
            for start in range(stop):
                check = inspect_interval(inspection, start, stop)
                intervals.append(dict(original_checker=check,
                    eligible_under_existing_contract=bool(check['allowed'] and decision.get('selection') is not None),
                    supporting_observation_nodes=[n['id'] for n in program['nodes'][start:stop] if observations[n['id']]]))
    result = dict(kind='common-evidence-admission-review/v1', decision=decision,
        unit_evidence=unit_evidence, conflicts=conflicts, intervals=intervals,
        inspection_unavailable_costs=incomplete, source_unit_count=source_count,
        external_boundary=dict(node=marker, values=outputs, analysis_only=True, included_in_selector_input=False),
        creates_new_backend=False, authorizes_new_semantics=False,
        limits='Same interval checker and existing manual catalog authority; annotations can expose conflict, never prove purity. Eligible intervals need not have an implemented executable layout. Unbound inputs have no global plan/objective.')
    if 'candidate_implementations' in description:
        from candidate_bindings import review_candidate
        result['candidate_reviews'] = [review_candidate(description, candidate, root)
                                       for candidate in description['candidate_implementations']]
    return result
