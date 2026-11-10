import unittest

from call_dag import condense, from_extraction


def graph(nodes, pairs, roots):
    return {'nodes': [{'id': n} for n in nodes],
            'edges': [{'from': a, 'to': b} for a, b in pairs], 'roots': roots}


class CallDagTests(unittest.TestCase):
    def test_constructor_initialization_preserves_field_even_without_read(self):
        loc = {'file': 'real.java', 'start': 1, 'line': 1}
        data = {'methods': [dict(symbol=n, location=loc, source_available=True,
                    declared_throws=[], constructor=ctor, owner_type=owner)
                    for n, ctor, owner in [('App#main()', False, 'App'), ('Box#Box()', True, 'Box'),
                                            ('Dormant#Dormant()', True, 'Dormant')]],
                'sql_fields': [{'symbol': 'Box#query', 'location': loc, 'effect': 'unknown'}],
                'effects': [], 'initializers': [dict(symbol=owner+suffix, owner_type=owner,
                    static=static, location=loc, owner_identity_complete=True)
                    for owner in ['Box', 'Dormant']
                    for suffix, static in [('#<clinit>', True), ('#<instance-init>', False)]],
                'initialized_fields': [{'symbol': 'Box#query', 'initializer': 'Box#<instance-init>', 'location': loc}],
                'calls': [{'caller': 'App#main()', 'target_symbol': 'Box#Box()', 'location': loc,
                    'call_kind': 'new_class', 'dispatch': 'nonvirtual_constructor',
                    'result_use': {'kind': 'discarded'}, 'effect_summary_complete': False,
                    'sql_field_arguments': []}]}
        projected = from_extraction(data, ['real.java'], ['App#main()'])
        result = condense(projected)
        self.assertIn('Box#Box()', result['reachability_witnesses'])
        self.assertIn('Box#query', result['reachability_witnesses'])
        self.assertIn('Dormant#<clinit>', projected['roots'])
        self.assertNotIn('Dormant#<instance-init>', result['reachability_witnesses'])
        self.assertEqual(next(n['kind'] for n in projected['nodes'] if n['id']=='Box#query'), 'sql_field')

    def test_recursion_and_shared_provider(self):
        result = condense(graph(['root', 'a', 'b', 'shared', 'outside'],
            [('root', 'a'), ('a', 'b'), ('b', 'a'), ('b', 'shared'), ('outside', 'shared')], ['root']))
        self.assertEqual(result['membership']['a'], result['membership']['b'])
        self.assertEqual(result['outside_represented_paths'], ['outside'])
        self.assertEqual(result['classification']['shared'], 'retain_possible_dependency')
        position = {n: i for i, n in enumerate(result['topological_order'])}
        self.assertTrue(all(position[e['from']] < position[e['to']] for e in result['edges']))

    def test_extra_root_keeps_previously_outside_module(self):
        base = graph(['app', 'terminal', 'display'], [('terminal', 'display')], ['app'])
        self.assertIn('display', condense(base)['outside_represented_paths'])
        base['roots'].append('terminal')
        self.assertNotIn('display', condense(base)['outside_represented_paths'])

    def test_unknown_and_self_recursion_never_authorize_deletion(self):
        result = condense(graph(['root', 'unknown', 'outside'], [('root', 'unknown'), ('outside', 'outside')], ['root']))
        self.assertEqual(result['classification']['outside'], 'unknown_outside_incomplete_graph')
        self.assertTrue(any(c['cyclic'] for c in result['components']))
        self.assertFalse(result['deletion_authorized'])

    def test_long_chain_and_invalid_endpoint(self):
        ids = [str(i) for i in range(1500)]
        result = condense(graph(ids, list(zip(ids, ids[1:])), ['0']))
        self.assertEqual(len(result['reachability_witnesses']), 1500)
        with self.assertRaises(ValueError):
            condense(graph(['a'], [('a', 'missing')], ['a']))

    def test_ignored_result_reflection_sql_and_fixture_isolation(self):
        location = {'file': 'real.java', 'start': 20, 'line': 3}
        data = {'methods': [{'symbol': 'app', 'location': location, 'source_available': True, 'declared_throws': []},
                            {'symbol': 'fixture', 'location': {'file': 'fixture.java'}, 'source_available': True, 'declared_throws': []}],
                'sql_fields': [{'symbol': 'sql', 'location': location, 'effect': 'unknown'}],
                'effects': [{'method': 'app', 'kind': 'heap_write_possible', 'location': location}],
                'calls': [{'caller': 'app', 'target_symbol': 'invoke', 'location': location,
                    'dispatch': 'nonvirtual_symbol', 'result_use': {'kind': 'discarded'}, 'effect_summary_complete': False,
                    'reflection': {'targets': ['display']}, 'sql_field_arguments': ['sql']}]}
        projected = from_extraction(data, ['real.java'], ['app'])
        result = condense(projected)
        self.assertIn('display', result['reachability_witnesses'])
        self.assertIn('sql', result['reachability_witnesses'])
        self.assertNotIn('fixture', result['classification'])
        self.assertTrue(any(n['kind'] == 'unknown_call_effect_or_dispatch' for n in projected['nodes']))
        effect = next(n for n in projected['nodes'] if n['kind'] == 'effect_obligation')
        self.assertEqual(effect['effect']['kind'], 'heap_write_possible')
        self.assertIn(effect['id'], result['reachability_witnesses'])


if __name__ == '__main__':
    unittest.main()
