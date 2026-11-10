"""Strategy-independent orchestration. No deletion/fusion/project imports."""
from copy import deepcopy
from dataclasses import asdict
import math

from .api import (CandidateProvider, EvidenceStore, MeasurementProvider,
                  MeasuredPlan, Record, SelectionPolicy, SemanticValidator,
                  Verdict, fingerprint)


class PlanningFramework:
    def __init__(self, *, store: EvidenceStore,
                 validators: dict[str, SemanticValidator],
                 measurements: MeasurementProvider, policy: SelectionPolicy,
                 min_blocks: int = 6):
        if type(min_blocks) is not int or min_blocks < 2:
            raise ValueError('min_blocks must be an integer >= 2')
        if {'original', 'composition'} & validators.keys():
            raise ValueError('Original and composition envelopes are framework-owned')
        self.store = store
        self.validators = dict(validators)
        self.measurements = measurements
        self.policy = policy
        self.min_blocks = min_blocks

    def plan(self, *, context: Record, original: Record,
             providers: tuple[CandidateProvider, ...], engine_bindings: list[Record]) -> dict:
        """Discover candidates through providers, then validate, measure and rank.

        Provider failures abort discovery: silently selecting from an incomplete
        search would hide a failed experiment. Candidate-level failures abstain.
        """
        context = deepcopy(dict(context))
        self._bindings(engine_bindings)
        if original.get('id') != 'original':
            raise ValueError('Baseline must have ID original')
        candidates = [deepcopy(dict(original))]
        for provider in providers:
            for candidate in provider.propose(deepcopy(context), self.store):
                candidates.append(deepcopy(dict(candidate)))
        return self.select_catalog(dict(schema='adaptive-plan-portfolio/v1',
            context=context, candidates=candidates, engine_bindings=engine_bindings))

    def _bindings(self, bindings):
        if not bindings:
            raise ValueError('Missing engine bindings')
        for ref in bindings:
            self.store.read(ref)

    def select_catalog(self, catalog: Record) -> dict:
        """Backward-compatible JSON input; output is adaptive-framework-result/v1."""
        catalog = deepcopy(dict(catalog))
        if catalog.get('schema') != 'adaptive-plan-portfolio/v1':
            raise ValueError('Unknown portfolio schema')
        self._bindings(catalog.get('engine_bindings'))
        context = catalog['context']
        digest = fingerprint(context)
        candidates = {c['id']: c for c in catalog['candidates']}
        if len(candidates) != len(catalog['candidates']) or 'original' not in candidates:
            raise ValueError('Missing original or duplicate candidate ID')
        admission = {}

        def admit(name, active=()):
            if name in active:
                raise ValueError('Composition cycle')
            if name in admission:
                return admission[name]['status'] == 'eligible'
            if name not in candidates:
                raise ValueError('Missing component: ' + name)
            c = candidates[name]
            try:
                if c['context'] != digest or c['authority'] != 'reviewed_experiment':
                    raise ValueError('Context or authority mismatch')
                family = c['family']
                if name == 'original':
                    if family != 'original' or c.get('components') or c.get('gates'):
                        raise ValueError('Invalid original plan')
                else:
                    if not c.get('regions') or len(set(c['regions'])) != len(c['regions']):
                        raise ValueError('Missing or duplicate source regions')
                    if family == 'composition':
                        children = c.get('components', [])
                        if len(children) < 2 or len(set(children)) != len(children) or 'original' in children:
                            raise ValueError('Invalid composition')
                        occupied = set()
                        for child in children:
                            if not admit(child, active + (name,)):
                                raise ValueError('Ineligible component: ' + child)
                            regions = set(candidates[child]['regions'])
                            if regions & occupied:
                                raise ValueError('Overlapping regions require reanalysis')
                            occupied.update(regions)
                        if occupied != set(c['regions']) or c['validation'].get('components') != children:
                            raise ValueError('Missing exact joint validation or coverage')
                    elif family not in self.validators:
                        raise ValueError('No registered validator for family: ' + family)
                    elif c.get('components'):
                        raise ValueError('Leaf plan cannot claim composition components')
                if not c.get('artifacts'):
                    raise ValueError('No executable artifact')
                for ref in c['artifacts']:
                    self.store.read(ref)
                validation = c['validation']
                if validation['context'] != digest or not validation.get('evidence'):
                    raise ValueError('Missing context-bound validation')
                for ref in validation['evidence']:
                    if self.store.document(ref).get('status') not in ['pass', 'passed']:
                        raise ValueError('Validation did not pass')
                verdict = Verdict('allow', 'Bound original or jointly validated composition')
                if name != 'original' and family != 'composition':
                    verdict = self.validators[family].validate(deepcopy(context), deepcopy(c), self.store)
                    if not isinstance(verdict, Verdict) or verdict.status not in ['allow', 'retain', 'unknown']:
                        raise ValueError('Invalid validator response')
                admission[name] = dict(status='eligible' if verdict.status == 'allow' else 'ineligible',
                                       verdict=asdict(verdict))
            except Exception as error:
                # Trusted plugin exceptions fail closed and remain visible.
                admission[name] = dict(status='ineligible', reason=f'{type(error).__name__}: {error}')
            return admission[name]['status'] == 'eligible'

        for name in candidates:
            admit(name)
        if admission['original']['status'] != 'eligible':
            raise ValueError('Original binding invalid; no verified fallback')

        def complexity(name):
            c = candidates[name]
            if name == 'original':
                return 0
            return (sum(complexity(x) for x in c['components'])
                    if c['family'] == 'composition' else 1)

        costs, rejected = {}, {}
        expected_blocks = None
        # Baseline first, independent of input ordering; failures cannot be repaired
        # by measurements from an unrelated candidate or a policy's optimism.
        for name in ['original'] + sorted(n for n in candidates if n != 'original'):
            if admission[name]['status'] != 'eligible':
                continue
            if name != 'original' and 'original' not in costs:
                rejected[name] = 'No comparable baseline cost'
                continue
            try:
                c = candidates[name]
                cost = self.measurements.measure(deepcopy(context), deepcopy(c), self.store)
                if cost is None:
                    raise ValueError('No measurement')
                if cost['context'] != digest or cost['unit'] != 'ms':
                    raise ValueError('Incomparable context or unit')
                blocks, values = cost['blocks'], cost['values']
                if (len(blocks) != len(set(blocks)) or len(values) != len(blocks)
                        or len(values) < self.min_blocks
                        or (name != 'original' and blocks != expected_blocks)):
                    raise ValueError('Missing, unpaired or insufficient blocks')
                if any(type(v) not in [float, int] or not math.isfinite(v) or v <= 0 for v in values):
                    raise ValueError('Invalid latency sample')
                if not cost.get('evidence'):
                    raise ValueError('Unbound measurement')
                bound = self.store.document(cost['binding'])
                if bound != {k: cost[k] for k in ['context', 'unit', 'blocks', 'values']}:
                    raise ValueError('Samples differ from bound measurement')
                for ref in cost['evidence']:
                    self.store.read(ref)
                if name == 'original':
                    expected_blocks = deepcopy(blocks)
                costs[name] = MeasuredPlan(name, c['family'], complexity(name),
                                           tuple(blocks), tuple(values))
            except Exception as error:
                rejected[name] = f'{type(error).__name__}: {error}'

        selected, reason, details = 'original', 'No comparable baseline cost', {}
        if 'original' in costs:
            try:
                choice = self.policy.choose(deepcopy(context), tuple(costs.values()))
                if choice.selected not in costs:
                    raise ValueError('Policy chose a plan outside the admitted measured set')
                selected, reason, details = choice.selected, choice.reason, deepcopy(dict(choice.details))
            except Exception as error:
                reason = f'Policy failed; retained original: {type(error).__name__}: {error}'
        return dict(schema='adaptive-framework-result/v1', selected=selected, reason=reason,
                    context=context, admission=admission, rejected_costs=rejected,
                    measured_plans=sorted(costs), policy_details=details,
                    selected_artifacts=deepcopy(candidates[selected]['artifacts']),
                    automatic_deployment=False)
