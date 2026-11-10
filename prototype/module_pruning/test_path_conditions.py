import unittest
from itertools import product

from path_conditions import analyze_graph, evaluate


def atom(key, values, op='in', kind='boolean'):
    return {'variable': {'id': key, 'type': kind}, 'operator': op, 'values': values}


def summary(*atoms, unknowns=None):
    return {'atoms': list(atoms), 'unknowns': unknowns or [], 'full_path_condition_complete': False}


class PathConditionTests(unittest.TestCase):
    def test_boolean_conjunction_against_exhaustive_oracle(self):
        # Finite algorithm check, not a restriction of application data.
        predicates = [atom(k, [v], op) for k in ('a', 'b') for v in (False, True) for op in ('in', 'not_in')]
        for left, right in product(predicates, repeat=2):
            satisfiable = any(all((values[p['variable']['id']] in p['values']) == (p['operator']=='in')
                                  for p in (left, right))
                              for bits in product((False, True), repeat=2)
                              for values in [dict(zip(('a','b'), bits))])
            actual = evaluate(summary(left, right))['status']
            self.assertEqual(actual == 'excluded_by_necessary_guards', not satisfiable)

    def test_string_default_and_unbounded_domain(self):
        default = atom('s', ['east','west'], 'not_in', 'java.lang.String')
        self.assertEqual(evaluate(summary(default))['status'], 'not_refuted')
        self.assertEqual(evaluate(summary(default), {'s':['east']})['status'], 'excluded_by_necessary_guards')
        self.assertEqual(evaluate(summary(default), {'s':['unseen-value', None]})['status'], 'not_refuted')

    def test_unknown_does_not_mean_false(self):
        opaque = [{'reason':'unsolved'}]
        self.assertEqual(evaluate(summary(unknowns=opaque))['status'], 'unknown')
        conflict = summary(atom('x',[True]), atom('x',[False]), unknowns=opaque)
        self.assertEqual(evaluate(conflict)['status'], 'excluded_by_necessary_guards')
        self.assertEqual(evaluate(None)['status'], 'unknown')

    def test_scope_identity_and_context_validation(self):
        self.assertEqual(evaluate(summary(atom('method1:x',[True]),atom('method2:x',[False])))['status'], 'not_refuted')
        with self.assertRaises(ValueError):
            evaluate(summary(atom('x',[True])), {'x':[]})
        with self.assertRaises(ValueError):
            evaluate(summary(atom('x',[1])))

    def test_projection_preserves_nodes_unknowns_and_alternative_paths(self):
        edges = [{'from':'r','to':'m','evidence':{'conditions':c},'kind':'call'}
                 for c in [summary(atom('x',[True]),atom('x',[False])), summary(atom('x',[True])), None]]
        graph = {'nodes':[{'id':'r'},{'id':'m'}], 'roots':['r'], 'edges':edges}
        result = analyze_graph(graph)
        self.assertEqual(len(result['projection']['edges']), 2)
        self.assertEqual(result['projection']['nodes'], graph['nodes'])
        self.assertEqual(len(graph['edges']), 3)
        self.assertFalse(result['deletion_authorized'])
        with self.assertRaises(ValueError):
            analyze_graph(graph, {'typo':[True]})


if __name__ == '__main__':
    unittest.main()
