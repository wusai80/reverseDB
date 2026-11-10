"""Default implementations over frozen studies; replace through public protocols."""
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import statistics

from .api import Choice, EvidenceStore, Record, Verdict
from .framework import PlanningFramework
from .selector import POLICY, paired_comparison, replay_gate


@dataclass
class ArchivedCandidates:
    records: tuple[Record, ...]

    def propose(self, context, store):
        return deepcopy(self.records)


@dataclass
class ExistingGateValidator:
    kind: str

    def validate(self, context, candidate, store):
        gates = candidate.get('gates', [])
        if not gates or any(g['kind'] != self.kind for g in gates):
            return Verdict('unknown', 'Missing matching family checker')
        results = [replay_gate(g, store.root) for g in gates]
        if not all(g['allowed'] for g in results):
            return Verdict('unknown', 'Existing checker retains or abstains', {'gates': results})
        return Verdict('allow', 'Existing conditional checker admits candidate', {'gates': results})


class ArchivedMeasurements:
    def measure(self, context, candidate, store):
        return deepcopy(candidate.get('cost'))


class BootstrapPolicy:
    """Same development policy as the frozen selector; not semantic confidence."""
    def __init__(self, parameters=None):
        self.parameters = dict(POLICY if parameters is None else parameters)
        p = self.parameters
        if (type(p['min_blocks']) is not int or p['min_blocks'] < 2
                or type(p['bootstrap_samples']) is not int or p['bootstrap_samples'] < 100
                or not 0 < p['alpha'] < 0.5 or not 0 <= p['min_relative_gain'] < 1):
            raise ValueError('Invalid bootstrap policy')

    def choose(self, context, plans):
        by_id = {p.id: p for p in plans}
        if 'original' not in by_id or any(len(p.values_ms) < self.parameters['min_blocks'] for p in plans):
            raise ValueError('Missing original or insufficient measurements')
        comparisons, dominators = {}, {p.id: set() for p in plans}
        for a in plans:
            for b in plans:
                if a.id == b.id:
                    continue
                result = paired_comparison(a.values_ms, b.values_ms, self.parameters)
                comparisons[a.id + '>' + b.id] = result
                if result['beats']:
                    dominators[b.id].add(a.id)
        qualified = {p.id for p in plans if p.id == 'original' or comparisons[p.id + '>original']['beats']}
        frontier = sorted(n for n in qualified if not (dominators[n] & qualified))
        selected = min(frontier, key=lambda n: (by_id[n].leaf_count,
            statistics.mean(math.log(v) for v in by_id[n].values_ms), n))
        return Choice(selected, 'Least complex plan not robustly outperformed',
                      dict(frontier=frontier, comparisons=comparisons, parameters=self.parameters))


def reviewed_framework(root, *, policy=None, measurements=None, validators=None):
    """Convenience wiring; pass other implementations or construct the core directly."""
    selected_policy = BootstrapPolicy() if policy is None else policy
    return PlanningFramework(store=EvidenceStore(Path(root)),
        validators=({'deletion': ExistingGateValidator('deletion'),
                     'fusion': ExistingGateValidator('fusion')} if validators is None else validators),
        measurements=ArchivedMeasurements() if measurements is None else measurements,
        policy=selected_policy)
