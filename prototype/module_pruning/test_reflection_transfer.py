import unittest
from copy import deepcopy

from interprocedural_conditions import reflection_calls, analyze


def fixture():
    contract={'symbol':'bridge', 'method_sha256':'hash-bound-fixture'}
    call={'caller':'caller','target_symbol':'bridge','location':{'file':'f','start':1},
          'conditions':{'atoms':[],'unknowns':[]},
          'reflection':{'targets':['target'],'receiver_types':['Box'],'ambiguous_receiver_types':[],'no_matching_method':[]},
          'reflection_argument_transfer':{'status':'verified_bridge_target','target':'target',
            'parameter_count':1, 'body_available':True,
            'bridge_symbol':'bridge','bridge_method_sha256':contract['method_sha256'],
            'bindings':[{'position':0,'bridge_argument_index':2,'type_compatible':True,'formal':None,'actual':{'kind':'unknown'}}]}}
    return {'reflection_bridge':contract},call


class ReflectionTransferTests(unittest.TestCase):
    def test_verified_view_preserves_original_call(self):
        data,call=fixture(); original=deepcopy(call)
        expanded,rejected=reflection_calls(data,[call])
        self.assertEqual(expanded[0]['target_symbol'],'target')
        self.assertEqual(call,original)
        self.assertFalse(rejected)

    def test_hash_target_index_and_type_mismatch_rejected(self):
        for mutate in [lambda p:p.update(bridge_method_sha256='changed'),lambda p:p.update(target='different'),
                       lambda p:p['bindings'][0].update(bridge_argument_index=0),
                       lambda p:p['bindings'][0].update(type_compatible=False),
                       lambda p:p.update(bindings=[])]:
            data,call=fixture();mutate(call['reflection_argument_transfer'])
            expanded,rejected=reflection_calls(data,[call])
            self.assertFalse(expanded);self.assertEqual(rejected[0]['status'],'unknown')

    def test_reflection_recursion_does_not_transfer_context(self):
        data,call=fixture();call['caller']='target'
        data.update(methods=[{'symbol':'target','location':{'file':'f'}},{'symbol':'bridge','location':{'file':'f'}}],calls=[call])
        result=analyze(data,['f'],include_reflection=True)
        self.assertIn('target',result['recursive_methods'])
        self.assertTrue(all(r['status']=='unknown' for r in result['records']))
        self.assertEqual(result['global_edges_removed'],0)


if __name__=='__main__':unittest.main()
