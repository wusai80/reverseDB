"""Select among reviewed executable alternatives; never learn semantic legality.

Deletion and fusion keep their existing checkers. This layer compares complete,
validated plans using paired latency evidence for ONE exact context. It neither
adds component speedups nor generates code nor authorizes automatic deployment.
"""
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from prototype.module_pruning.demand_model import decide
from placement_frontend import select_complete_contract

POLICY = dict(min_blocks=6, bootstrap_samples=4000, alpha=0.05,
              min_relative_gain=0.02, seed=1729)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_ref(ref, root):
    root = Path(root).resolve()
    path = (root / ref['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Evidence outside repository')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('Changed artifact: ' + ref['path'])
    return data


def document(ref, root):
    return json.loads(read_ref(ref, root))


def replay_gate(gate, root):
    payload = document(gate['input'], root)
    if gate['kind'] == 'deletion':
        result = decide(payload['graph'], payload['facts'], payload['removed'], payload['obligations'])
        allowed = result['decision'] == 'conditionally_deletable'
    elif gate['kind'] == 'fusion':
        result = select_complete_contract(payload)
        allowed = result.get('selection') is not None and bool(result['selection']['database_regions'])
    else:
        raise ValueError('Unsupported legality checker')
    if 'expected' in gate:
        expected = document(gate['expected'], root)
        if result != expected:
            raise ValueError('Existing checker result changed')
    return dict(allowed=allowed, checker=gate['kind'], result=result)


def check_candidates(catalog, root):
    candidates = {c['id']: c for c in catalog['candidates']}
    if len(candidates) != len(catalog['candidates']) or 'original' not in candidates:
        raise ValueError('Missing original or duplicate candidate')
    context = fingerprint(catalog['context'])
    results = {}

    def visit(name, active=()):
        if name in active:
            raise ValueError('Composition cycle')
        if name in results:
            return results[name]
        if name not in candidates:
            raise ValueError('Missing component')
        c = candidates[name]
        try:
            if c['context'] != context:
                raise ValueError('Contract/source/workload/deployment context mismatch')
            if c['authority'] != 'reviewed_experiment':
                raise ValueError('Unsupported authority; no new semantic proof is inferred')
            family = c['family']
            gate_results = []
            if name == 'original':
                if family != 'original' or c.get('components') or c.get('gates'):
                    raise ValueError('Invalid original plan')
            elif family in ['deletion', 'fusion']:
                if not c.get('gates') or any(g['kind'] != family for g in c['gates']):
                    raise ValueError('Missing family checker')
                gate_results = [replay_gate(g, root) for g in c['gates']]
                if not all(g['allowed'] for g in gate_results):
                    results[name] = dict(status='ineligible', reason='existing_checker_retains_or_abstains', gates=gate_results)
                    return results[name]
            elif family == 'composition':
                components = c.get('components', [])
                if len(components) < 2 or len(set(components)) != len(components) or 'original' in components:
                    raise ValueError('Invalid component list')
                occupied = set()
                for child in components:
                    result = visit(child, active + (name,))
                    if result['status'] != 'eligible':
                        raise ValueError('Ineligible component: ' + child)
                    regions = set(candidates[child]['regions'])
                    if occupied & regions:
                        raise ValueError('Overlapping regions require reanalysis')
                    occupied.update(regions)
                if occupied != set(c['regions']):
                    raise ValueError('Composition coverage mismatch')
                if c['validation'].get('components') != components:
                    raise ValueError('Missing validation of this exact composition')
            else:
                raise ValueError('Unsupported candidate family')
            if name != 'original' and (not c.get('regions') or len(set(c['regions'])) != len(c['regions'])):
                raise ValueError('Missing or duplicate source regions')
            if not c.get('artifacts'):
                raise ValueError('No concrete executable artifact')
            for ref in c['artifacts']:
                read_ref(ref, root)
            validation = c['validation']
            if validation['context'] != context or not validation.get('evidence'):
                raise ValueError('Missing context-bound validation')
            for ref in validation['evidence']:
                if document(ref, root).get('status') not in ['pass', 'passed']:
                    raise ValueError('Validation did not pass')
            results[name] = dict(status='eligible', gates=gate_results,
                                 authority='Conditional reviewed experiment; not universal equivalence')
        except (ValueError, KeyError, TypeError, OSError) as error:
            results[name] = dict(status='ineligible', reason=str(error))
        return results[name]

    for name in candidates:
        visit(name)
    if results['original']['status'] != 'eligible':
        raise ValueError('Original binding invalid; cannot provide a safe fallback')
    return results


def paired_comparison(faster, slower, policy):
    """Lower bootstrap bound of paired log speedup; descriptive, not a proof."""
    gains = [math.log(b) - math.log(a) for a, b in zip(faster, slower)]
    observed = statistics.mean(gains)
    rng = random.Random(policy['seed'])
    draws = sorted(statistics.mean(rng.choices(gains, k=len(gains)))
                   for _ in range(policy['bootstrap_samples']))
    # Clamp to observed mean so robust-dominance edges strictly improve the
    # point estimate; cycles cannot arise from a bootstrap artifact.
    lower = min(observed, draws[int(policy['alpha'] * len(draws))])
    ratio = lambda value: math.exp(value) if -700 < value < 700 else None
    return dict(geometric_speedup=ratio(observed), lower_speedup=ratio(lower),
                mean_log_speedup=observed, lower_log_speedup=lower,
                beats=lower > math.log1p(policy['min_relative_gain']),
                interpretation='Exploratory paired-bootstrap bound; not calibrated confidence in correctness')


def composition_opportunities(catalog, admission):
    """Propose disjoint cross-family pairs; joint validation is still mandatory."""
    leaves = [c for c in catalog['candidates'] if c['family'] in ['deletion','fusion']
              and admission[c['id']]['status'] == 'eligible']
    existing = {frozenset(c.get('components', [])): c['id'] for c in catalog['candidates']
                if c['family'] == 'composition'}
    proposals = []
    for a,b in itertools.combinations(leaves,2):
        if a['family'] == b['family'] or set(a['regions']) & set(b['regions']):
            continue
        components = sorted([a['id'],b['id']])
        name = existing.get(frozenset(components))
        proposals.append(dict(components=components,existing_plan=name,
            status='jointly_validated_alternative' if name and admission[name]['status']=='eligible'
                   else 'needs_joint_construction_validation_and_measurement',
            permits_execution=False))
    return proposals


def select(catalog, root, policy=None):
    if catalog.get('schema') != 'adaptive-plan-portfolio/v1':
        raise ValueError('Unknown portfolio schema')
    if not catalog.get('engine_bindings'):
        raise ValueError('Missing version bindings for existing checkers')
    for ref in catalog['engine_bindings']:
        read_ref(ref, root)
    policy = dict(POLICY if policy is None else policy)
    if (type(policy['min_blocks']) is not int or policy['min_blocks'] < 2
            or type(policy['bootstrap_samples']) is not int or policy['bootstrap_samples'] < 100
            or not 0 < policy['alpha'] < 0.5 or not 0 <= policy['min_relative_gain'] < 1):
        raise ValueError('Invalid ranking policy')
    admission = check_candidates(catalog, root)
    candidates = {c['id']: c for c in catalog['candidates']}
    context = fingerprint(catalog['context'])
    costs = {}; rejected_costs = {}
    original_cost = candidates['original'].get('cost')
    expected_blocks = original_cost.get('blocks') if isinstance(original_cost, dict) else None
    for name, c in candidates.items():
        if admission[name]['status'] != 'eligible':
            continue
        try:
            cost = c['cost']
            if cost['context'] != context or cost['unit'] != 'ms':
                raise ValueError('Incomparable measurement context or units')
            blocks, values = cost['blocks'], cost['values']
            if (blocks != expected_blocks or len(blocks) != len(set(blocks))
                    or len(values) != len(blocks) or len(values) < policy['min_blocks']):
                raise ValueError('Missing, unpaired or insufficient blocks')
            if any(type(v) not in [float, int] or not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError('Invalid latency sample')
            if not cost.get('evidence'):
                raise ValueError('Unbound cost estimate')
            bound = document(cost['binding'], root)
            if bound != {key: cost[key] for key in ['context', 'unit', 'blocks', 'values']}:
                raise ValueError('Samples differ from bound measurement record')
            for ref in cost['evidence']:
                read_ref(ref, root)
            costs[name] = values
        except (ValueError, KeyError, TypeError, OSError) as error:
            rejected_costs[name] = str(error)
    comparisons = {}; dominators = {n: [] for n in costs}
    if 'original' not in costs:
        chosen = 'original'; frontier = ['original']
        reason = ('no_eligible_replacement' if not any(n != 'original' and r['status'] == 'eligible'
                  for n, r in admission.items()) else 'no_comparable_baseline_cost')
    else:
        for a in costs:
            for b in costs:
                if a == b:
                    continue
                comparison = paired_comparison(costs[a], costs[b], policy)
                comparisons[a + '>' + b] = comparison
                if comparison['beats']:
                    dominators[b].append(a)
        qualified = {n for n in costs if n == 'original' or comparisons[n + '>original']['beats']}
        frontier = sorted(n for n in qualified if not (set(dominators[n]) & qualified))
        def complexity(name):
            c = candidates[name]
            if name == 'original':return 0
            return sum(complexity(child) for child in c['components']) if c['family'] == 'composition' else 1
        chosen = min(frontier, key=lambda n: (complexity(n), statistics.mean(map(math.log, costs[n])), n))
        reason = 'least_complex_plan_not_robustly_outperformed'
    return dict(schema='adaptive-plan-selection/v1', selected=chosen, reason=reason,
                context=catalog['context'], policy=policy, admission=admission,
                rejected_costs=rejected_costs, comparisons=comparisons, frontier=frontier,
                composition_opportunities=composition_opportunities(catalog,admission),
                selected_artifacts=candidates[chosen]['artifacts'], automatic_deployment=False,
                limits='Best-supported choice among supplied validated plans for this context, not a global optimum. No code generation, semantic proof or unseen-workload prediction.')
