import ast
import copy
import json
import unittest

from .model import Contract, MAX_I64, OutOfDomain, Unsupported, observe
from .rewrite import propose, check
from .source_codegen import generate
from .source_examples import INPUTS, bundles
from .source_frontend import extract, verify_binding
from .source_runtime import invoke, load_original


class SourcePipelineTests(unittest.TestCase):
    def setUp(self):
        self.bundle = bundles()['cross_module']
        self.entry = ('app','run')
        self.inputs = dict(key=0,other=1,value=7,enabled=True)
        self.state = {0:4,1:8,999:123}

    def build(self,bundle=None,contract=Contract()):
        bundle = bundle or self.bundle
        p,w = extract(bundle,self.entry,INPUTS)
        q,c = propose(p,contract)
        fn,code,record = generate(bundle,self.entry,INPUTS,p,w,q,c,contract)
        original = load_original(bundle,self.entry,INPUTS,p,w)
        return p,w,q,original,fn,code,record

    def test_actual_original_and_emitted_code(self):
        p,w,q,original,fn,code,record = self.build()
        self.assertEqual([o['op'] for o in p['ops']],['read','pack','field'])
        self.assertEqual([o['op'] for o in q['ops']],['read'])
        self.assertEqual(len(w['call_edges']),3)
        self.assertEqual({o['module'] for o in p['ops']},{'storage','model','consumer'})
        self.assertNotIn('model.',code)
        self.assertNotIn('consumer.',code)
        self.assertFalse(record['sql_fusion'])
        self.assertEqual(invoke(original,p,w,self.inputs,self.state)['observation'],invoke(fn,q,w,self.inputs,self.state,generated=True)['observation'])

    def test_source_and_witness_tampering_rejected(self):
        p,w = extract(self.bundle,self.entry,INPUTS)
        q,c = propose(p)
        changed = dict(self.bundle,consumer=self.bundle['consumer'].replace('[0]','[1]'))
        self.assertFalse(verify_binding(changed,self.entry,INPUTS,p,w))
        for call in [lambda:load_original(changed,self.entry,INPUTS,p,w),lambda:generate(changed,self.entry,INPUTS,p,w,q,c,Contract())]:
            with self.assertRaises(Unsupported): call()
        altered = copy.deepcopy(w); altered['locations']['read']['line'] += 1
        self.assertFalse(verify_binding(self.bundle,self.entry,INPUTS,p,altered))
        altered = copy.deepcopy(q); altered['outputs'] = ['arg:value']
        with self.assertRaises(Unsupported): generate(self.bundle,self.entry,INPUTS,p,w,altered,c,Contract())

    def test_bool_integer_literal_mutants_are_not_equal_syntax(self):
        for literal, mutant in [('1',True), ('True',1), ('0',False), ('False',0)]:
            source={'app':'def run(rt, value):\n    return '+literal+'\n'}
            p,w=extract(source,self.entry,{'value':'i64'})
            q,c=propose(p)
            q['ops'][0]['value']=mutant
            self.assertFalse(check(p,q,c)['accepted'])
            self.assertFalse(verify_binding(source,self.entry,{'value':'i64'},q,w))
            with self.assertRaises(Unsupported): generate(source,self.entry,{'value':'i64'},p,w,q,c,Contract())

    def test_serialized_witness_preserves_argument_order(self):
        p,w,q,original,fn,code,record = self.build()
        w = json.loads(json.dumps(w,sort_keys=True))
        p = json.loads(json.dumps(p,sort_keys=True))
        types = json.loads(json.dumps(INPUTS,sort_keys=True))
        self.assertTrue(verify_binding(self.bundle,self.entry,types,p,w))
        original = load_original(self.bundle,self.entry,types,p,w)
        self.assertEqual(invoke(original,p,w,self.inputs,self.state)['observation']['outcome'],('ok',(4,)))

    def test_unsupported_python_is_not_assumed_pure(self):
        bodies = [
            'return value + 1',                    # Python integer semantics differ.
            'return key is other',                 # Identity observation.
            'return [key, value]',                 # Mutable packet.
            'return rt.unknown(key)',              # Unknown effect.
            'return rt',                           # Resource escape.
            'return rt[key]',                       # Resource access.
            'return value[0]',                     # Non-packet access.
            'return (value,)[9]',                   # Potential IndexError.
            'return key == enabled',               # bool is not i64.
            'return abs(value)',                    # Unbound callable.
            'return (lambda x: x)(value)',
            'return storage.load(rt, key=key)',
            'for x in (key,):\n        value = x\n    return value',
            'if enabled:\n        return value\n    return key',
            'try:\n        return value\n    except:\n        return key',
            'alias = rt\n    return alias.read("site", key)',
            'row = storage.load(rt, key)\n    storage = value\n    return row',
            'row = storage.load(rt, key)\n    again = storage.load(rt, other)\n    return row', # duplicate fault site
            'return run(rt, key, other, value, enabled)', # recursion
            'return 9223372036854775808',
        ]
        for body in bodies:
            bundle = dict(self.bundle,app='import storage\ndef run(rt, key, other, value, enabled):\n    '+body+'\n')
            with self.subTest(body=body), self.assertRaises(Unsupported): extract(bundle,self.entry,INPUTS)

    def test_module_execution_and_signature_rejected(self):
        changes = [
            'side_effect()\n'+self.bundle['app'],
            'import os\n'+self.bundle['app'],
            self.bundle['app'].replace('def run','@decorate\ndef run'),
            self.bundle['app'].replace('enabled):','enabled=side_effect()):'),
            self.bundle['app'].replace('value, enabled','value: unknown, enabled'),
            self.bundle['app'].replace('def run','class Receiver: pass\n\ndef run'),
            self.bundle['app'].replace('key, other, value, enabled','key, key, value, enabled'),
        ]
        for text in changes:
            with self.subTest(source=text), self.assertRaises(Unsupported): extract(dict(self.bundle,app=text),self.entry,INPUTS)

    def test_faults_guards_and_full_state(self):
        p,w,q,original,fn,code,record = self.build(bundles()['guard_and_rollback'])
        for function,program,generated in [(original,p,False),(fn,q,True)]:
            result = invoke(function,program,w,dict(self.inputs,enabled=False),self.state,generated=generated)
            self.assertEqual(result['observation'],dict(outcome=('error','denied'),state=self.state,events=[('attempt',(7,))]))
            result = invoke(function,program,w,self.inputs,self.state,{'write':''},generated=generated)
            self.assertEqual(result['observation']['outcome'],('error',''))
            self.assertEqual(result['observation']['state'],self.state)

    def test_effectful_tuple_keeps_operand_evaluation(self):
        p,w,q,original,fn,code,record = self.build(bundles()['effectful_packet'])
        self.assertNotIn('pack',[o['op'] for o in q['ops']])
        self.assertEqual([o['op'] for o in q['ops']],['read','read','const','add'])
        for function,program,generated in [(original,p,False),(fn,q,True)]:
            out = invoke(function,program,w,self.inputs,{0:MAX_I64,999:123},{'extra':'first'},generated=generated)['observation']
            self.assertEqual(out['outcome'],('error','first'))
            out = invoke(function,program,w,self.inputs,{0:MAX_I64,999:123},generated=generated)['observation']
            self.assertEqual(out['outcome'],('error','i64-overflow'))

    def test_read_fault_exclusion_is_explicit(self):
        p,w,q,original,fn,code,record = self.build(bundles()['dead_read'],Contract(False))
        self.assertNotIn('unused',[o['id'] for o in q['ops']])
        before = invoke(original,p,w,self.inputs,self.state,contract=Contract(False))
        after = invoke(fn,q,w,self.inputs,self.state,contract=Contract(False),generated=True)
        self.assertEqual(before['observation'],after['observation'])
        self.assertEqual((before['work']['reads'],after['work']['reads']),(2,1))
        with self.assertRaises(OutOfDomain): invoke(original,p,w,self.inputs,self.state,{'unused':'read-denied'},Contract(False))

    def test_type_boundary_rejects_bool_as_integer(self):
        p,w,q,original,fn,code,record = self.build()
        for function,program,generated in [(original,p,False),(fn,q,True)]:
            with self.assertRaises(OutOfDomain): invoke(function,program,w,dict(self.inputs,key=True),self.state,generated=generated)

    def test_module_function_and_variable_renaming(self):
        # Rename symbols structurally, never change runtime API or metadata.
        mapping = dict(storage='repository',model='representation',consumer='presentation',app='application',
                       load='fetch',build='assemble',take='select',run='entry',row='datum',packet='carrier',result='answer')
        class Rename(ast.NodeTransformer):
            def visit_Name(self,node): node.id=mapping.get(node.id,node.id); return node
            def visit_arg(self,node): node.arg=mapping.get(node.arg,node.arg); return node
            def visit_FunctionDef(self,node): node.name=mapping.get(node.name,node.name); return self.generic_visit(node)
            def visit_Attribute(self,node): node.attr=mapping.get(node.attr,node.attr); return self.generic_visit(node)
            def visit_alias(self,node): node.name=mapping.get(node.name,node.name); return node
        renamed = {mapping[k]:ast.unparse(Rename().visit(ast.parse(v)))+'\n' for k,v in self.bundle.items()}
        p,w = extract(renamed,('application','entry'),INPUTS)
        q,c = propose(p)
        fn,code,r = generate(renamed,('application','entry'),INPUTS,p,w,q,c,Contract())
        self.assertEqual([o['op'] for o in q['ops']],['read'])
        self.assertEqual(invoke(fn,q,w,self.inputs,self.state,generated=True)['observation']['outcome'],('ok',(4,)))


if __name__ == '__main__': unittest.main()
