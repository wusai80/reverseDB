import copy
import unittest

from .fixtures import op, programs
from .model import Contract, MIN_I64, MAX_I64, OutOfDomain, Unsupported, bound, observe, validate
from .rewrite import check, discover_regions, propose
from .sqlite_backend import execute


class SemanticsTests(unittest.TestCase):
    def setUp(self):
        self.examples = programs()
        self.inputs = dict(key=0, other=1, value=7, enabled=True)
        self.state = {0:4, 1:9, 999:123}  # Also preserve data outside the input key domain.

    def test_projection_and_escaping_value(self):
        for name, packet_count in [('packet_projection', 0), ('packet_escape', 1), ('nested_packet', 0)]:
            source = self.examples[name]
            before = copy.deepcopy(source)
            candidate, cert = propose(source)
            self.assertTrue(check(source, candidate, cert)['accepted'])
            self.assertEqual(before, source)
            self.assertEqual(sum(o['op'] == 'pack' for o in candidate['ops']), packet_count)
            self.assertEqual(observe(source, self.inputs, self.state)['observation'], execute(candidate, self.inputs, self.state)['observation'])

    def test_effects_and_rollback(self):
        source = self.examples['guard_event_write']
        candidate, cert = propose(source)
        self.assertTrue(check(source, candidate, cert)['accepted'])
        self.assertEqual([o['op'] for o in candidate['ops']], ['emit', 'write', 'check', 'read'])
        for executor in (observe, execute):
            result = executor(candidate, dict(self.inputs, enabled=False), self.state)['observation']
            self.assertEqual(result, dict(outcome=('error', 'denied'), state=self.state, events=[('attempt', (7,))]))

    def test_read_contract_is_not_inferred_from_samples(self):
        source = self.examples['dead_read']
        strict, strict_cert = propose(source)
        normal, normal_cert = propose(source, Contract(False))
        self.assertTrue(check(source, strict, strict_cert)['accepted'])
        self.assertTrue(check(source, normal, normal_cert, Contract(False))['accepted'])
        self.assertIn('unused', [o['id'] for o in strict['ops']])
        self.assertNotIn('unused', [o['id'] for o in normal['ops']])
        self.assertFalse(check(source, normal, normal_cert)['accepted'])
        # The excluded behavior is a real counterexample, not equivalent success.
        self.assertEqual(observe(source, self.inputs, self.state, {'unused':'read-denied'})['observation']['outcome'], ('error', 'read-denied'))
        self.assertEqual(observe(normal, self.inputs, self.state)['observation']['outcome'][0], 'ok')
        with self.assertRaises(OutOfDomain): observe(source, self.inputs, self.state, {'unused':'read-denied'}, Contract(False))
        with self.assertRaises(OutOfDomain): Contract(0)

    def test_dead_overflow_cannot_be_erased(self):
        source = self.examples['checked_add']
        candidate, cert = propose(source)
        self.assertTrue(check(source, candidate, cert)['accepted'])
        self.assertIn('unused_add', [o['id'] for o in candidate['ops']])
        for executor in (observe, execute):
            self.assertEqual(executor(candidate, dict(self.inputs, value=MAX_I64), self.state)['observation']['outcome'], ('error', 'i64-overflow'))
        mutant = copy.deepcopy(source)
        mutant['ops'] = [o for o in mutant['ops'] if o['id'] != 'unused_add']
        forged = dict(schema='local-rewrite-certificate/v1', source=bound(source, Contract()), steps=[dict(kind='erase', site='unused_add')])
        self.assertFalse(check(source, mutant, forged)['accepted'])
        negative = copy.deepcopy(source)
        negative['ops'][0]['value'] = -1
        for executor in (observe, execute):
            self.assertEqual(executor(negative, dict(self.inputs, value=MIN_I64), self.state)['observation']['outcome'], ('error', 'i64-overflow'))

    def test_boundary_internalization_and_effect_barrier(self):
        source = self.examples['packet_projection']
        regions = {(r['start'],r['stop']):r for r in discover_regions(source)}
        self.assertEqual(regions[0,2]['reasons'], ['missing_boundary_codec'])
        self.assertTrue(regions[0,3]['admissible'])
        local = {(r['start'],r['stop']):r for r in discover_regions(source, local_only=True)}
        self.assertIn('original_module_boundary', local[0,3]['reasons'])
        event_regions = discover_regions(self.examples['effect_after_read'])
        self.assertTrue(all('unsupported_target_operation' in r['reasons'] for r in event_regions if r['start'] <= 1 < r['stop']))
        self.assertTrue(all(not r['generated_backend'] for r in regions.values()))

    def test_reordered_read_is_observably_wrong(self):
        source = self.examples['read_write_read']
        mutant = copy.deepcopy(source)
        mutant['ops'][0], mutant['ops'][1] = mutant['ops'][1], mutant['ops'][0]
        validate(mutant)  # Well typed is insufficient.
        cert = dict(schema='local-rewrite-certificate/v1', source=bound(source, Contract()), steps=[])
        self.assertFalse(check(source, mutant, cert)['accepted'])
        self.assertNotEqual(observe(source, self.inputs, self.state)['observation'], observe(mutant, self.inputs, self.state)['observation'])

    def test_forged_certificates_and_semantic_flags(self):
        source = self.examples['packet_projection']
        candidate, cert = propose(source)
        for field, value in [('source','wrong'), ('steps', [dict(kind='trust',site='r')]), ('schema','wrong')]:
            forged = dict(cert, **{field:value})
            self.assertFalse(check(source, candidate, forged)['accepted'])
        bad = copy.deepcopy(candidate); bad['outputs'] = ['value']
        self.assertFalse(check(source, bad, cert)['accepted'])
        bad = copy.deepcopy(source); bad['ops'][0]['verified'] = True
        self.assertFalse(check(bad, candidate, cert)['accepted'])
        forged = dict(cert, steps=[dict(kind='erase',site='r')])
        self.assertFalse(check(source, candidate, forged)['accepted'])

    def test_unsupported_inputs_abstain(self):
        for mutation in ('unknown', 'index', 'alias', 'unresolved'):
            bad = copy.deepcopy(self.examples['packet_projection'])
            if mutation == 'unknown': bad['ops'][0]['op'] = 'callback'
            if mutation == 'index': bad['ops'][2]['index'] = 9
            if mutation == 'alias': bad['inputs']['key'] = 'mutable_object'
            if mutation == 'unresolved': bad['ops'][0]['args'] = ['not_defined']
            with self.assertRaises(Unsupported): propose(bad)

    def test_empty_error_code_and_first_failure_order(self):
        source = self.examples['read_write_read']
        for executor in (observe, execute):
            result = executor(source, self.inputs, self.state, {'first':'', 'w':'later'})['observation']
            self.assertEqual(result['outcome'], ('error',''))
            self.assertEqual(result['state'], self.state)

    def test_effect_erasure_rejected_even_without_value_consumer(self):
        source = self.examples['guard_event_write']
        for site in ['before', 'w', 'guard']:
            mutant = copy.deepcopy(source)
            mutant['ops'] = [o for o in mutant['ops'] if o['id'] != site]
            cert = dict(schema='local-rewrite-certificate/v1', source=bound(source, Contract()), steps=[dict(kind='erase',site=site)])
            self.assertFalse(check(source, mutant, cert)['accepted'])

    def test_alpha_and_module_renaming(self):
        for source in self.examples.values():
            expected, cert = propose(source)
            for prefix in ['a_', 'different_', 'another_']:
                def rename(p):
                    p = copy.deepcopy(p)
                    p['inputs'] = {prefix+k:v for k,v in p['inputs'].items()}
                    p['outputs'] = [prefix+v for v in p['outputs']]
                    for i, o in enumerate(p['ops']):
                        o['id'] = prefix+o['id']
                        o['module'] = prefix+o['module']
                        o['args'] = [prefix+a for a in o['args']]
                        if 'out' in o: o['out'] = prefix+o['out']
                    return p
                renamed = rename(source)
                actual, proof = propose(renamed)
                self.assertEqual(actual, rename(expected))
                self.assertTrue(check(renamed, actual, proof)['accepted'])


if __name__ == '__main__': unittest.main()
