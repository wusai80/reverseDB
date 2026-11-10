"""Fixed semantic composition experiment; no timed runs or default patching."""
import argparse
from contextlib import ExitStack
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch

from .checked_ir import catalog_record,propose,compile_program,validate
from .test_checked_ir import CheckedIRTests,operation as op,identity
from .fixtures import RenamedLazy
from .lowering import Unsupported


def save(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def demand_program(keys):
    ops=[op('new','lazy_new',out='h',binding='lazy',raw='row',converter='convert')]
    for i,k in enumerate(keys):ops.append(op('d'+str(i),'lazy_get',['h'],out='x'+str(i),key=k))
    if keys:
        ops.append(op('pack','pack',['x'+str(i) for i in range(len(keys))],out='p'))
        ops.append(op('field','field',['p'],out='first',index=0))
    return dict(inputs={},ops=ops,outputs=['first','x'+str(len(keys)-1)] if keys else [])


def beets_program(init,fields):
    ops=[op('fixed','lazy_new',out='h',binding='lazy',raw='fixed',converter='convert'),
         op('flex','lazy_new',out='flex_h',binding='lazy',raw='flexible',converter='convert'),
         op('registry_init','invoke',out='registry',callee='registry'),
         op('init_demand','lazy_get',['h'],out='init_value',key=init),
         op('prefix','invoke',['init_value'],out='prefix_done',callee='prefix'),
         op('clear','invoke',out='clear_done',callee='clear')]
    for i,key in enumerate(fields):
        ops.append(op('registry'+str(i),'invoke',out='registry'+str(i),callee='registry'))
        ops.append(op('d'+str(i),'lazy_get',['h'],out='x'+str(i),key=key))
    # A value carrier intentionally spans the initialization/consumer boundary.
    ops.append(op('packet','pack',['init_value']+['x'+str(i) for i in range(len(fields))],out='packet'))
    outputs=[]
    for i in range(len(fields)):
        name='result'+str(i);outputs.append(name)
        ops.append(op(name,'field',['packet'],out=name,index=i+1))
    return dict(inputs={},ops=ops,outputs=outputs)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    root=Path.cwd();out=args.out;out.mkdir(parents=True,exist_ok=False);checks=[]
    def check(name,truth):
        if not truth:raise AssertionError(name)
        checks.append(name)
    files=[root/'prototype/lazy_state'/n for n in ['ownership.py','checked_ir.py','test_checked_ir.py','verify_checked_ir.py']]
    files += [root/'prototype/lazy_state/lowering.py',root/'scripts/initialization_effects.py',root/'prototype/beets/prefix_fixtures.py']
    try:
        for frozen_name in ['lazy-state-freeze.json','source-pipeline-freeze.json']:
            frozen=json.loads((root/'paper'/frozen_name).read_text())
            check(frozen_name+' unchanged',all(sha(root/p)==h for p,h in frozen['sha256'].items()))
        test_result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CheckedIRTests))
        save(out/'tests.json',dict(tests=test_result.testsRun,failures=[str(x) for x in test_result.failures],errors=[str(x) for x in test_result.errors]))
        check('ownership and rewrite tests pass',test_result.wasSuccessful())
        save(out/'protocol.json',dict(component_keys=['a','b','missing'],maximum_demand_length=3,raw_presence_patterns=4,
            converter_modes=['value','none','object','error'],original_application_fixture_rows=100,
            performance=False,source_extraction='Lazy constructor and lookup bound; surrounding beets IR assembled explicitly from prior prefix/state plans',
            checked_property='Private handle confinement within the closed extended IR; no claim about arbitrary Python introspection'))
        sequences=[s for n in range(4) for s in itertools.product(['a','b','missing'],repeat=n)]
        count=0
        for bits in itertools.product([False,True],repeat=2):
            raw={k:v for k,v,b in zip(['a','b'],[1,2],bits) if b}
            for keys,mode in itertools.product(sequences,['value','none','object','error']):
                records=[]
                for original in [True,False]:
                    events=[]
                    def convert(key,value):
                        events.append(key)
                        if mode=='error':raise ValueError('conversion:'+key)
                        return None if mode=='none' else [key,value] if mode=='object' else value
                    p=demand_program(keys);resources=dict(bindings={'lazy':RenamedLazy},raws={'row':dict(raw)},converters={'convert':convert},calls={})
                    catalog=catalog_record(**resources)
                    q,c=(p,identity(p,catalog)) if original else propose(p,catalog)
                    fn,_,_=compile_program(p,q,c,catalog,**resources,original=original)
                    try:
                        values=fn(None,{});record=dict(values=values,alias=len(values)==2 and values[0] is values[1])
                    except Exception as error:record=dict(error=type(error).__name__,message=str(error))
                    records.append(dict(outcome=record,events=events,raw=resources['raws']['row']))
                check('component '+str(count),records[0]==records[1]);count+=1
        sys.path.insert(0,str(root/'scripts'));sys.path.insert(0,str(root/'prototype/beets'))
        source=root/'work/beets/source-e978874d';sys.path.insert(0,str(source))
        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n');os.environ['BEETSDIR']=str((out/'config').resolve())
        from beets import plugins
        from beets.dbcore import db as core
        from beets.library import Item,Library
        from initialization_effects import recover_empty_mapping_prefix,recover_private_state_step
        from test_backend_boundaries import VALUES,value_repr
        from test_beets_feasibility import snapshot
        import prefix_fixtures as effects
        base=root/'evidence/beets-feasibility/20261108-a03fafe1c20f'
        check('pinned application source unchanged',all(sha(source/p)==h for p,h in json.loads((base/'source-manifest.json').read_text()).items()))
        check('no plugins',not list(plugins.find_plugins()))
        prefix_plan=recover_empty_mapping_prefix(Item,'update');state_plan=recover_private_state_step(Item,'clear_dirty')
        check('one initialization demand in supported composition',len(prefix_plan['field_reads'])==1)
        init=prefix_plan['field_reads'][0];target='tracktotal'
        ns={};exec(prefix_plan['code'],ns);prefix=ns['run_initialization_prefix']
        ns={};exec(state_plan['code'],ns);clear=ns['run_private_state_step']
        save(out/'source-prefix-plan.json',prefix_plan);save(out/'source-state-plan.json',state_plan)
        path=out/'library.db';baseline_hash=sha(base/'100/library.db')
        with sqlite3.connect(base/'100/library.db') as src,sqlite3.connect(path) as dst:src.backup(dst)
        lib=Library(path,directory=base/'100/music');before=snapshot(path)
        packet=lib.items();rows,flex=list(packet.rows),list(packet.flex_rows)
        indexed=core.Results(Item,list(rows),lib,list(flex))._get_indexed_flex_attrs()
        def original(row,resource,fields):
            item=core.Results(Item,[row],resource,list(flex))[0]
            return tuple(getattr(item,k) for k in fields)
        def compile_candidate(row,resource,fields,clear_fn=clear):
            required=[init]+list(fields);getters=Item._getters()
            if any(k not in Item._fields or k in getters or k not in row.keys() for k in required):raise Unsupported('Region requires unsupported computed/missing field behavior')
            state={'_db':resource,'_dirty':set()}
            def registry():
                current=Item._getters()
                if any(k in current for k in required):raise Unsupported('Getter registry changed; no fallback after effects')
                return current
            def prefix_value(value):return prefix(lambda key:value)
            resources=dict(bindings={'lazy':core.LazyDict},raws={'fixed':{k:v for k,v in dict(row).items() if k[:4]!='flex'},'flexible':indexed.get(row['id'],{}) or {}},
                           converters={'convert':Item._convert},calls={'registry':registry,'prefix':prefix_value,'clear':lambda:clear_fn(state)})
            p=beets_program(init,fields);catalog=catalog_record(**resources);q,c=propose(p,catalog)
            fn,code,admission=compile_program(p,q,c,catalog,**resources)
            return fn,dict(source=p,candidate=q,certificate=c,catalog=catalog,admission=admission),code,state
        def outcome(fn):
            try:return dict(values=[value_repr(v) for v in fn()])
            except Exception as e:return dict(error=type(e).__name__,message=str(e))
        all_rows=[]
        for row in rows:
            fn,artifact,code,state=compile_candidate(row,lib,['id',target,init,target])
            a=outcome(lambda:original(row,lib,['id',target,init,target]));b=outcome(lambda:fn(None,{}))
            check('application row '+str(row['id']),a==b);all_rows.append(dict(id=row['id'],original=a,candidate=b))
        check('full 100 rows and complete fields',len(rows)==100 and all(len(r.keys())==len(Item._fields) for r in rows))
        save(out/'all-rows.json',all_rows);save(out/'checked-plan.json',artifact);(out/'generated-region.py').write_text(code)
        numeric=[]
        for name,value in VALUES:
            if name=='empty':continue  # Empty Results uses original path; outside this nonempty region.
            row=dict(rows[0],**{target:value});fn,_,_,_=compile_candidate(row,lib,[target])
            a=outcome(lambda:original(row,lib,[target]));b=outcome(lambda:fn(None,{}))
            check('decode '+name,a==b);numeric.append(dict(case=name,original=a,candidate=b))
        save(out/'decode-cases.json',numeric)
        baseconvert=Item._convert;basegetters=Item._getters
        modes=['normal','init-error','target-error','eq-true','eq-false','eq-error','truth-error','resource-false','resource-truth-error','resource-revision-error','repeat-init','repeat-target']
        effect_records=[]
        for mode in modes:
            arms=[]
            for arm in ['original','candidate']:
                events=[];states=[];resource=effects.Resource(events,mode.removeprefix('resource-') if mode.startswith('resource-') else 'normal')
                fields=[init,init,target] if mode=='repeat-init' else [target,target] if mode=='repeat-target' else [target]
                def conversion(cls,key,value):
                    events.append(['convert',key])
                    if mode=='init-error' and key==init or mode=='target-error' and key==target:raise RuntimeError(key+' conversion failure')
                    if key==init and mode in ['eq-true','eq-false','eq-error','truth-error']:return effects.Comparable(events,mode.removeprefix('eq-') if mode in ['eq-true','eq-false'] else mode)
                    return baseconvert(key,value)
                def registry(cls):events.append('registry');return basegetters()
                def clear_original(obj):
                    core.Model.clear_dirty(obj);states.append(dict(dirty=len(obj._dirty),revision=obj.__dict__.get('_revision')))
                def clear_candidate(state):
                    clear(state);states.append(dict(dirty=len(state['_dirty']),revision=state.get('_revision')))
                with ExitStack() as stack:
                    stack.enter_context(patch.object(Item,'_convert',classmethod(conversion)))
                    # Binding/preflight is outside the measured execution trace.
                    if arm=='candidate':fn,_,_,_=compile_candidate(rows[0],resource,fields,clear_candidate)
                    stack.enter_context(patch.object(Item,'_getters',classmethod(registry)))
                    if arm=='original':stack.enter_context(patch.object(Item,'clear_dirty',clear_original))
                    result=outcome(lambda:original(rows[0],resource,fields)) if arm=='original' else outcome(lambda:fn(None,{}))
                arms.append(dict(outcome=result,events=events,state=states))
            check('effects '+mode,arms[0]==arms[1]);effect_records.append(dict(case=mode,arms=arms))
        save(out/'effect-cases.json',effect_records)
        fn,_,_,_=compile_candidate(rows[0],lib,[target])
        with patch.object(Item,'__init__',side_effect=AssertionError('Item constructed')),patch.object(core.LazyDict,'__init__',side_effect=AssertionError('LazyDict constructed')):
            result=fn(None,{})
        check('generated region constructs neither wrapper',result==original(rows[0],lib,[target]))
        check('complete database and baseline unchanged',snapshot(path)==before and len(before['tables'])==5 and sha(base/'100/library.db')==baseline_hash)
        lib._close();save(out/'database-state.json',dict(before=before,after=snapshot(path)))
        report=dict(status='pass',test_groups=test_result.testsRun,component_pairs=count,application_rows=len(rows),decode_cases=len(numeric),effect_cases=len(effect_records),
                    extended_ir_checked=True,old_rewrite_checker_reused=True,private_handle_flow_checked=True,lazy_constructor_checked=True,
                    beets_region_assembly='explicit development adapter using source-derived prefix/state plans',whole_item_constructor_proved=False,
                    arbitrary_callback_nonescape_proved=False,performance_measured=False,held_out_transfer=False,checks=checks)
        save(out/'summary.json',report);print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
    except BaseException as error:
        save(out/'failure.json',dict(type=type(error).__name__,message=str(error),checks=checks));raise
    finally:
        for p in files:
            dst=out/'inputs'/p.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
        save(out/'source-manifest.json',{str(p.relative_to(root)):sha(p) for p in files})
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
