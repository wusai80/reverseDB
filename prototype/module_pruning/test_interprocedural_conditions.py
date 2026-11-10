import unittest
from copy import deepcopy
from itertools import product

from interprocedural_conditions import compose, analyze


def var(id):
    return {'id': id, 'type': 'boolean'}


def conditions(id, value):
    return {'atoms': [{'variable': var(id), 'operator': 'in', 'values': [value]}], 'unknowns': []}


def pair(left=True, right=False):
    outer = {'caller': 'caller', 'target_symbol': 'callee', 'location': {'file': 'f', 'start': 1},
             'conditions': conditions('x', left), 'argument_transfer': {'status': 'direct_source_target',
             'target': 'callee', 'bindings': [{'formal': var('p'), 'actual': {'kind': 'stable_variable', 'variable': var('x')}}]}}
    inner = {'caller': 'callee', 'target_symbol': 'hit', 'location': {'file': 'f', 'start': 2},
             'conditions': conditions('p', right)}
    return outer, inner


class InterproceduralConditionTests(unittest.TestCase):
    def test_substitution_against_boolean_oracle(self):
        for left, right in product((False, True), repeat=2):
            outer, inner = pair(left, right)
            self.assertEqual(compose(outer, inner)['status']=='excluded_by_necessary_guards', left != right)
        outer, inner = pair()
        original = deepcopy((outer, inner)); compose(outer, inner)
        self.assertEqual((outer, inner), original)

    def test_literal_and_unbound_local_are_distinct(self):
        outer, inner = pair()
        outer['argument_transfer']['bindings'][0]['actual'] = {'kind': 'literal', 'type':'boolean', 'value': True}
        self.assertEqual(compose(outer, inner)['status'], 'excluded_by_necessary_guards')
        inner['conditions'] = conditions('x', False)  # Same spelling, distinct callee local.
        self.assertEqual(compose(outer, inner)['status'], 'not_refuted')

    def test_unknown_dispatch_mutation_and_deferred_keep_unknown(self):
        for reason in ('dynamic_dispatch_not_closed', 'external_or_varargs_target'):
            outer, inner = pair(); outer['argument_transfer']={'status':'unknown','reason':reason}
            self.assertEqual(compose(outer, inner)['status'], 'unknown')
        outer, inner = pair(); outer['argument_transfer']['bindings'][0]['actual']={'kind':'unknown'}
        self.assertEqual(compose(outer, inner)['status'], 'unknown')
        outer, inner = pair(); inner['conditions']['unknowns']=[{'reason':'deferred_boundary_outer_conditions_not_inherited'}]
        self.assertEqual(compose(outer, inner)['status'], 'unknown')

    def test_alternative_contexts_not_merged_and_recursion_unknown(self):
        outer, inner = pair(); other=deepcopy(outer); other['location']['start']=3;other['conditions']=conditions('x',False)
        data={'methods':[{'symbol':s,'location':{'file':'f'}} for s in ['caller','callee','hit']], 'calls':[outer,other,inner]}
        result=analyze(data,['f'])
        self.assertEqual([r['status'] for r in result['records']], ['excluded_by_necessary_guards','not_refuted'])
        self.assertEqual(result['global_edges_removed'],0)
        recurse=deepcopy(inner);recurse['target_symbol']='caller';data['calls'].append(recurse)
        result=analyze(data,['f'])
        self.assertTrue(all(r['status']=='unknown' for r in result['records']))
        self.assertFalse(result['deletion_authorized'])


if __name__=='__main__':
    unittest.main()
