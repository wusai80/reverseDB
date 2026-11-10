from copy import deepcopy
import unittest

from prototype.region_semantics import model
from .checked_ir import catalog_record,project,propose,check,validate,compile_program
from .fixtures import RenamedLazy
from .lowering import Unsupported
from .ownership import constructor_binding


def operation(ident,kind,args=(),**extra):return dict(id=ident,op=kind,module='m',args=list(args),**extra)


def example(keys=('a','a')):
    ops=[operation('new','lazy_new',out='h',binding='lazy',raw='row',converter='convert')]
    for i,key in enumerate(keys):ops.append(operation('get'+str(i),'lazy_get',['h'],out='x'+str(i),key=key))
    ops += [operation('pack','pack',['x'+str(i) for i in range(len(keys))],out='p'),operation('field','field',['p'],out='v',index=0)] if keys else []
    return dict(inputs={},ops=ops,outputs=['v'] if keys else [])


def identity(program,catalog):
    return dict(schema='private-lazy-ir/v1',source=model.fingerprint(program),catalog=model.fingerprint(catalog),
                scalar_certificate=dict(schema='local-rewrite-certificate/v1',source=model.bound(project(program),model.Contract(True)),steps=[]))


class ExtraConstructor(RenamedLazy):
    def __init__(self,payload,decode):
        super().__init__(payload,decode)
        decode('audit',None)


class CheckedIRTests(unittest.TestCase):
    def setUp(self):
        self.bindings={'lazy':RenamedLazy};self.raws={'row':{'a':1,'b':2}}
        self.events=[]
        def convert(k,v):self.events.append(k);return [k,v]
        self.converters={'convert':convert};self.calls={'audit':lambda v:self.events.append('audit')}
        self.catalog=catalog_record(self.bindings,self.raws,self.converters,self.calls)

    def test_same_checker_removes_packet_but_keeps_all_demands(self):
        p=example();q,c=propose(p,self.catalog)
        self.assertTrue(check(p,q,c,self.catalog)['accepted'])
        self.assertEqual([o['op'] for o in q['ops']],['lazy_new','lazy_get','lazy_get'])
        fn,code,_=compile_program(p,q,c,self.catalog,self.bindings,self.raws,self.converters,self.calls)
        self.assertEqual(fn(None,{}),(['a',1],));self.assertEqual(self.events,['a'])
        self.assertNotIn('@effect-anchor',code)
        self.assertNotIn('rt.read',code)

    def test_escaping_handle_paths_rejected(self):
        for mode in ['return','pack','callback','emit','write','field']:
            p=example()
            if mode=='return':p['outputs']=['h']
            elif mode=='pack':p['ops'][3]['args']=['h']
            elif mode=='callback':p['ops'].append(operation('call','invoke',['h'],out='bad',callee='audit'))
            elif mode=='emit':p['ops'].append(operation('event','emit',['h'],label='escape'))
            elif mode=='write':p['ops'].append(operation('write','write',['h','h']))
            else:p['ops'].append(operation('bad','field',['h'],out='bad',index=0))
            with self.subTest(mode=mode),self.assertRaises(Unsupported):validate(p,self.catalog)

    def test_dead_demand_failure_cannot_be_erased(self):
        p=example(('a','b'));q,c=propose(p,self.catalog)
        bad=deepcopy(q);bad['ops']=[o for o in bad['ops'] if o['id']!='get1']
        self.assertFalse(check(p,bad,c,self.catalog)['accepted'])
        def fail(k,v):
            if k=='b':raise ValueError('late conversion')
            return v
        fn,_,_=compile_program(p,q,c,self.catalog,self.bindings,self.raws,{'convert':fail},self.calls)
        with self.assertRaisesRegex(ValueError,'late conversion'):fn(None,{})

    def test_mutated_effect_metadata_and_order_rejected(self):
        p=example(('a','b'));q,c=propose(p,self.catalog)
        for change in ['key','raw','converter','order','delete']:
            bad=deepcopy(q)
            if change=='key':bad['ops'][1]['key']='b'
            elif change=='raw':bad['ops'][0]['raw']='different'
            elif change=='converter':bad['ops'][0]['converter']='unknown'
            elif change=='order':bad['ops'][1],bad['ops'][2]=bad['ops'][2],bad['ops'][1]
            else:bad['ops'].pop()
            self.assertFalse(check(p,bad,c,self.catalog)['accepted'],change)

    def test_callback_with_public_value_is_retained(self):
        p=example();p['ops'].append(operation('call','invoke',['v'],out='unused',callee='audit'))
        q,c=propose(p,self.catalog)
        self.assertEqual(q['ops'][-1]['args'],['x0'])
        fn,_,_=compile_program(p,q,c,self.catalog,self.bindings,self.raws,self.converters,self.calls)
        fn(None,{});self.assertEqual(self.events,['a','audit'])
        bad=deepcopy(p);bad['ops'][-1]['callee']='unregistered'
        with self.assertRaises(Unsupported):validate(bad,self.catalog)

    def test_opaque_values_cannot_be_claimed_i64(self):
        p=example();p['ops'].append(operation('add','add',['v','v'],out='n'))
        with self.assertRaises(Unsupported):validate(p,self.catalog)

    def test_constructor_effect_and_catalog_tampering_rejected(self):
        with self.assertRaises(Unsupported):constructor_binding(ExtraConstructor)
        p=example();q,c=propose(p,self.catalog)
        forged=deepcopy(self.catalog);forged['bindings']['lazy']['fresh_cache']=False
        self.assertFalse(check(p,q,c,forged)['accepted'])
        with self.assertRaises(Unsupported):compile_program(p,q,c,forged,self.bindings,self.raws,self.converters,self.calls)

    def test_scalar_type_mutation_stays_rejected(self):
        p=dict(inputs={},ops=[operation('c','const',out='x',value=1)],outputs=['x'])
        q,c=propose(p,self.catalog);q['ops'][0]['value']=True
        self.assertFalse(check(p,q,c,self.catalog)['accepted'])

    def test_returned_value_identity_is_preserved(self):
        p=example();p['outputs']=['x0','x1']
        for original in [True,False]:
            q,c=(p,identity(p,self.catalog)) if original else propose(p,self.catalog)
            fn,_,_=compile_program(p,q,c,self.catalog,self.bindings,self.raws,self.converters,self.calls,original=original)
            a,b=fn(None,{})
            self.assertIs(a,b)


if __name__=='__main__':unittest.main()
