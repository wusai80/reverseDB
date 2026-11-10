"""API substitution and safety tests; synthetic costs are not benchmarks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prototype.adaptive_planning import Choice, EvidenceStore, PlanningFramework, Verdict
from prototype.adaptive_planning.api import fingerprint
from prototype.adaptive_planning.adapters import (ArchivedCandidates, ArchivedMeasurements,
    BootstrapPolicy, reviewed_framework)


class AllowValidator:
    def validate(self, context, candidate, store):
        return Verdict('allow', 'Synthetic fixture only')


class UnknownValidator:
    def validate(self, context, candidate, store):
        return Verdict('unknown', 'Unresolved effect')


class RetainPolicy:
    def choose(self, context, plans):
        return Choice('original', 'Alternative policy intentionally retains baseline')


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reversedb-api-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = dict(source='synthetic', contract='all-data-retained', workload='fixed',
                            deployment='test', timing_boundary='whole-plan')
        self.digest = fingerprint(self.context)
        self.counter = 0
        self.artifact = self.record({'executable_fixture': True})
        self.validation = self.record({'status': 'passed', 'synthetic': True})
        self.original = self.candidate('original', 'original', 100)
        self.custom = self.candidate('custom', 'third_strategy', 40)
        self.catalog = dict(schema='adaptive-plan-portfolio/v1', context=self.context,
            engine_bindings=[self.artifact], candidates=[self.original, self.custom])

    def record(self, value):
        self.counter += 1
        path = self.root / f'{self.counter}.json'
        path.write_text(json.dumps(value))
        return dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def candidate(self, name, family, cost):
        payload = dict(context=self.digest, unit='ms', blocks=list(range(8)), values=[cost]*8)
        binding = self.record(payload)
        return dict(id=name, family=family, context=self.digest, authority='reviewed_experiment',
            regions=[] if name == 'original' else [name], artifacts=[self.artifact], gates=[],
            validation=dict(context=self.digest, evidence=[self.validation]),
            cost=dict(payload, binding=binding, evidence=[binding]))

    def framework(self, *, validators=None, measurements=None, policy=None):
        return PlanningFramework(store=EvidenceStore(self.root),
            validators={'third_strategy': AllowValidator()} if validators is None else validators,
            measurements=ArchivedMeasurements() if measurements is None else measurements,
            policy=BootstrapPolicy() if policy is None else policy)

    def test_archived_default_choices_and_comparisons(self):
        for name in ['fossil-hash', 'fossil-branch', 'tpcc-mixed', 'oscar-prefix']:
            with self.subTest(name=name):
                folder = ROOT/'evidence/adaptive-planning/20261110-v2'/name
                old = json.loads((folder/'selection.json').read_text())
                result = reviewed_framework(ROOT).select_catalog(json.loads((folder/'catalog.json').read_text()))
                self.assertEqual(result['selected'], old['selected'])
                self.assertEqual(result['policy_details'].get('comparisons', {}), old['comparisons'])
                self.assertEqual({k:v['status'] for k,v in result['admission'].items()},
                                 {k:v['status'] for k,v in old['admission'].items()})

    def test_third_family_via_provider_without_core_change(self):
        result = self.framework().plan(context=self.context, original=self.original,
            providers=(ArchivedCandidates((self.custom,)),), engine_bindings=[self.artifact])
        self.assertEqual(result['selected'], 'custom')
        self.assertFalse(result['automatic_deployment'])

    def test_swappable_policy(self):
        self.assertEqual(self.framework(policy=RetainPolicy()).select_catalog(self.catalog)['selected'], 'original')

    def test_swappable_measurements(self):
        samples = {c['id']: c.pop('cost') for c in self.catalog['candidates']}
        class ExternalMeasurements:
            def measure(inner, context, candidate, store):
                return samples[candidate['id']]
        self.assertEqual(self.framework(measurements=ExternalMeasurements()).select_catalog(self.catalog)['selected'], 'custom')

    def test_unknown_not_measured_or_ranked(self):
        seen = []
        class RecordingMeasurements(ArchivedMeasurements):
            def measure(inner, context, candidate, store):
                seen.append(candidate['id'])
                return super().measure(context, candidate, store)
        result = self.framework(validators={'third_strategy': UnknownValidator()},
            measurements=RecordingMeasurements()).select_catalog(self.catalog)
        self.assertEqual(seen, ['original'])
        self.assertEqual(result['measured_plans'], ['original'])

    def test_unregistered_family_abstains(self):
        self.assertEqual(self.framework(validators={}).select_catalog(self.catalog)['selected'], 'original')

    def test_validator_exception_abstains(self):
        class Broken:
            def validate(inner, *args):
                raise RuntimeError('checker unavailable')
        result = self.framework(validators={'third_strategy': Broken()}).select_catalog(self.catalog)
        self.assertEqual(result['selected'], 'original')
        self.assertIn('checker unavailable', result['admission']['custom']['reason'])

    def test_policy_cannot_resurrect_ineligible_plan(self):
        class Unsafe:
            def choose(inner, context, plans):
                return Choice('custom', 'Pretend cost overrides semantics')
        result = self.framework(validators={'third_strategy': UnknownValidator()}, policy=Unsafe()).select_catalog(self.catalog)
        self.assertEqual(result['selected'], 'original')
        self.assertIn('outside the admitted measured set', result['reason'])

    def test_policy_exception_falls_back(self):
        class Broken:
            def choose(inner, *args):
                raise RuntimeError('ranker unavailable')
        self.assertEqual(self.framework(policy=Broken()).select_catalog(self.catalog)['selected'], 'original')

    def test_plugin_argument_mutation_is_isolated(self):
        original = deepcopy(self.catalog)
        class MutatingValidator:
            def validate(inner, context, candidate, store):
                context.clear()
                candidate['id'] = 'injected'
                candidate['cost']['values'] = [0.001]*8
                return Verdict('allow', 'Synthetic')
        result = self.framework(validators={'third_strategy': MutatingValidator()}).select_catalog(self.catalog)
        self.assertEqual(result['selected'], 'custom')
        self.assertEqual(self.catalog, original)

    def test_failed_report_cannot_be_overridden_by_validator(self):
        self.custom['validation']['evidence'] = [self.record({'status':'failed'})]
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'original')

    def test_invalid_original_stops(self):
        self.original['artifacts'] = [dict(self.artifact, sha256='0'*64)]
        with self.assertRaisesRegex(ValueError, 'Original binding invalid'):
            self.framework().select_catalog(self.catalog)

    def test_changed_context_rejects(self):
        self.custom['context'] = 'another-contract'
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'original')

    def test_invalid_costs_cannot_reach_policy(self):
        for values in [[float('nan')]*8, [0]*8, [-1]*8, [0.001]*8]:
            with self.subTest(values=values):
                self.custom['cost']['values'] = values
                result = self.framework().select_catalog(self.catalog)
                self.assertEqual(result['selected'], 'original')
                self.assertIn('custom', result['rejected_costs'])

    def test_incomparable_blocks_and_units(self):
        for field,value in [('blocks', list(range(1,9))), ('unit','calls'), ('context','other')]:
            with self.subTest(field=field):
                c = deepcopy(self.catalog)
                c['candidates'][1]['cost'][field] = value
                self.assertEqual(self.framework().select_catalog(c)['selected'], 'original')

    def test_missing_baseline_cost_skips_alternative_measurements(self):
        self.original.pop('cost')
        result = self.framework().select_catalog(self.catalog)
        self.assertEqual(result['selected'], 'original')
        self.assertEqual(result['measured_plans'], [])

    def composition(self):
        second = self.candidate('second', 'third_strategy', 60)
        joint = self.candidate('joint', 'composition', 10)
        joint.update(components=['custom','second'], regions=['custom','second'])
        joint['validation']['components'] = ['custom','second']
        self.catalog['candidates'].extend([second, joint])
        return joint

    def test_composition_of_registered_strategies(self):
        self.composition()
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'joint')

    def test_composition_needs_joint_evidence_and_cost(self):
        joint = self.composition()
        joint['validation'].pop('components')
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'custom')
        joint['validation']['components'] = ['custom','second']
        joint.pop('cost')
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'custom')

    def test_composition_overlap_and_cycles(self):
        joint = self.composition()
        joint['components'] = ['joint','custom']
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'custom')
        joint['components'] = ['custom','second']
        self.catalog['candidates'][2]['regions'] = ['custom']
        self.assertEqual(self.framework().select_catalog(self.catalog)['selected'], 'custom')

    def test_reserved_validators_cannot_override_core(self):
        with self.assertRaisesRegex(ValueError, 'framework-owned'):
            self.framework(validators={'original':AllowValidator()})

    def test_duplicate_provider_ids_stop(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.framework().plan(context=self.context, original=self.original,
                providers=(ArchivedCandidates((self.custom,self.custom)),), engine_bindings=[self.artifact])

    def test_missing_or_changed_engine_binding(self):
        for bindings in [[], [dict(self.artifact,sha256='0'*64)]]:
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                self.framework().select_catalog(dict(self.catalog,engine_bindings=bindings))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(APITests))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(dict(status='passed' if result.wasSuccessful() else 'failed',
            tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
            scope='API substitution, safety and archived parity; no new performance measurements'), indent=2)+'\n')
    sys.exit(0 if result.wasSuccessful() else 1)
