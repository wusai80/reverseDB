"""One bounded lazy-state experiment plus existing beets development integration."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch

from .lowering import bind,compile_demand,prepare,Unsupported,CODE
from .fixtures import RenamedLazy,ExtraEffect,EarlyCache,DynamicReceiver
from .beets_adapter import consume


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')


def trace(cls,binding,loader,raw,sequence,mode,original=False):
    events=[]; attempts={}; results=[]; outcomes=[]; snapshots=[]
    def convert(key,value):
        attempts[key]=attempts.get(key,0)+1;events.append([key,value,attempts[key]])
        if mode=='always-fail' or mode=='fail-once' and attempts[key]==1:raise ValueError('conversion:'+key)
        if mode=='none':return None
        if mode=='object':return [key,value]  # Repeated demand must return this exact instance.
        return value+10
    data=dict(raw)
    if original:
        obj=cls(data,convert);cache=getattr(obj,binding.cache)
        read=lambda key:obj[key]
    else:
        data,cache,convert=prepare(data,convert)
        read=lambda key:loader(data,cache,convert,key)
    for key in sequence:
        try:
            value=read(key);results.append(value);outcomes.append(dict(value=value,type=type(value).__name__))
        except Exception as error:outcomes.append(dict(error=type(error).__name__,message=str(error)))
        snapshots.append(json.loads(json.dumps(dict(raw=data,cache=cache))))
    aliases=[[a is b for b in results] for a in results]
    # Identity of immutable scalar objects is outside scope; check list aliases only.
    object_aliases=[[aliases[i][j] for j,b in enumerate(results) if type(b) is list] for i,a in enumerate(results) if type(a) is list]
    return dict(outcomes=outcomes,events=events,snapshots=snapshots,object_aliases=object_aliases)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    root=Path.cwd();out=args.out;out.mkdir(parents=True,exist_ok=False)
    checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    sys.path.insert(0,str(root/'scripts'));sys.path.insert(0,str(root/'prototype/beets'))
    source=root/'work/beets/source-e978874d';sys.path.insert(0,str(source))
    (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n')
    os.environ['BEETSDIR']=str((out/'config').resolve())
    from beets import plugins
    from beets.dbcore import db as core
    from beets.library import Item,Library
    from initialization_effects import recover_empty_mapping_prefix,recover_private_state_step
    from test_beets_feasibility import snapshot
    from test_backend_boundaries import VALUES,value_repr
    import prefix_fixtures as effects
    base=root/'evidence/beets-feasibility/20261108-a03fafe1c20f'
    files=list(Path(__file__).parent.glob('*.py'))
    files += [root/'scripts/initialization_effects.py',root/'scripts/test_beets_feasibility.py',root/'scripts/test_backend_boundaries.py',root/'prototype/beets/prefix_fixtures.py']
    frozen=json.loads((root/'paper/source-pipeline-freeze.json').read_text())
    check('previous source-pipeline freeze unchanged',all(sha(root/p)==h for p,h in frozen['sha256'].items()))
    check('whole pinned beets source unchanged',all(sha(source/p)==h for p,h in json.loads((base/'source-manifest.json').read_text()).items()))
    check('no plugins',not list(plugins.find_plugins()))
    save(out/'protocol.json',dict(scope='Lazy-state component refinement and scoped existing beets adapter; no new whole-program admission',
        raw_keys=['a','b'],sequence_keys=['a','b','absent'],maximum_sequence_length=4,
        converter_modes=['normal','none','object','fail-once','always-fail'],
        actual_data='Full 100-row/five-table development fixture, full fixed/flexible packets',
        no_timing=True,no_query_deletion=True,no_default_installation=True))
    try:
        bound=bind(core.LazyDict);demand=compile_demand(bound)
        try:compile_demand(replace(bound,raw='another_slot'))
        except Unsupported:check('forged source binding rejected',True)
        else:raise AssertionError('Forged binding admitted')
        with patch.object(core.LazyDict,'__missing__',ExtraEffect.__missing__):
            try:compile_demand(bound)
            except Unsupported:check('changed lookup dispatch rejected',True)
            else:raise AssertionError('Changed dispatch admitted')
        save(out/'binding.json',bound.witness);(out/'generated-demand.py').write_text(CODE)
        sequences=[s for n in range(5) for s in itertools.product(['a','b','absent'],repeat=n)]
        configs=[]
        for bits in itertools.product([False,True],repeat=2):
            raw={k:v for k,v,enabled in zip(['a','b'],[1,2],bits) if enabled}
            for mode in ['normal','none','object','fail-once','always-fail']:
                for seq in sequences:configs.append((raw,seq,mode))
        summaries=[]
        for cls in [core.LazyDict,RenamedLazy]:
            b=bind(cls);load=compile_demand(b)
            for raw,seq,mode in configs:
                original=trace(cls,b,load,raw,seq,mode,True);candidate=trace(cls,b,load,raw,seq,mode)
                if original!=candidate:
                    save(out/'component-mismatch.json',dict(raw=raw,sequence=seq,mode=mode,original=original,candidate=candidate))
                    raise AssertionError('Lazy state differs')
            summaries.append(dict(class_name=cls.__qualname__,cases=len(configs),binding=b.witness))
        save(out/'component-results.json',summaries)
        rejects=[]
        for cls in [ExtraEffect,EarlyCache,DynamicReceiver]:
            try:bind(cls)
            except Unsupported as error:rejects.append(dict(class_name=cls.__name__,reason=str(error)))
            else:raise AssertionError('Unsupported lazy implementation accepted')
        save(out/'rejections.json',rejects)
        check('three unsupported lazy structures rejected',len(rejects)==3)
        def no_cache(raw,cache,convert,key):return convert(key,raw[key]) if key in raw else None
        def cache_failure(raw,cache,convert,key):
            try:return demand(raw,cache,convert,key)
            except Exception:cache[key]=None;raise
        def none_is_miss(raw,cache,convert,key):
            if cache.get(key) is not None:return cache[key]
            if key in raw:cache[key]=convert(key,raw[key]);return cache[key]
            return None
        mutants=[]
        for name,loader,mode in [('drop-cache',no_cache,'object'),('cache-failure',cache_failure,'fail-once'),('none-as-miss',none_is_miss,'none')]:
            a=trace(core.LazyDict,bound,demand,{'a':1,'b':2},['a','a'],mode,True)
            b=trace(core.LazyDict,bound,loader,{'a':1,'b':2},['a','a'],mode)
            check(name+' counterexample detected',a!=b)
            mutants.append(dict(name=name,original=a,mutant=b))
        save(out/'mutation-counterexamples.json',mutants)
        prefix_plan=recover_empty_mapping_prefix(Item,'update');state_plan=recover_private_state_step(Item,'clear_dirty')
        save(out/'prefix-plan.json',prefix_plan);save(out/'state-plan.json',state_plan)
        ns={};exec(prefix_plan['code'],ns);prefix=ns['run_initialization_prefix']
        ns={};exec(state_plan['code'],ns);clear=ns['run_private_state_step']
        init=prefix_plan['field_reads'][0];target='tracktotal'
        getters=Item._getters()
        check('prefix and target demands native',all(k in Item._fields and k not in getters for k in [init,target,'id']))
        original_hash=sha(base/'100/library.db');dbpath=out/'library.db'
        with sqlite3.connect(base/'100/library.db') as src,sqlite3.connect(dbpath) as dst:src.backup(dst)
        lib=Library(dbpath,directory=base/'100/music');initial=snapshot(dbpath)
        packet=lib.items();rows,flex=list(packet.rows),list(packet.flex_rows)
        def original(rows,flex,resource,fields):
            obj=core.Results(Item,list(rows),resource,list(flex))[0]
            return tuple(getattr(obj,k) for k in fields)
        def candidate(rows,flex,resource,fields,clear_fn=clear):
            return consume(core,Item,rows,flex,resource,prefix,clear_fn,fields,demand,getters=getters)
        def outcome(fn):
            try:return dict(values=[value_repr(v) for v in fn()])
            except Exception as e:return dict(error=type(e).__name__,message=str(e))
        originals=[];candidates=[]
        for row in rows:
            originals.append(outcome(lambda:original([row],flex,lib,['id',target,init,target])))
            candidates.append(outcome(lambda:candidate([row],flex,lib,['id',target,init,target])[0]))
        check('all 100 full-row results and repeated demands equal',len(rows)==100 and originals==candidates)
        check('complete fixed and flexible packets preserved',all(len(r.keys())==len(Item._fields) for r in rows) and len(flex)>0)
        save(out/'all-rows.json',dict(original=originals,candidate=candidates,rows=len(rows),flex_rows=len(flex)))
        numeric=[]
        for name,value in VALUES:
            testrows=[] if name=='empty' else [dict(rows[0],**{target:value})]
            a=outcome(lambda:original(testrows,flex,lib,[target]));b=outcome(lambda:candidate(testrows,flex,lib,[target])[0])
            check('decode '+name,a==b);numeric.append(dict(case=name,original=a,candidate=b))
        save(out/'decode-cases.json',numeric)
        baseconvert=Item._convert;registry=Item._getters
        effect_cases=['normal','init-error','target-error','eq-true','eq-false','eq-error','truth-error','resource-false','resource-truth-error','resource-revision-error','repeat-init','repeat-target']
        effect_results=[]
        for case in effect_cases:
            arms=[]
            for arm in ['original','candidate']:
                events=[];state=[]
                resource=effects.Resource(events,case.removeprefix('resource-') if case.startswith('resource-') else 'normal')
                fields=[init,init,target] if case=='repeat-init' else [target,target] if case=='repeat-target' else [target]
                def conversion(cls,key,value):
                    events.append(['convert',key])
                    if case=='init-error' and key==init or case=='target-error' and key==target:raise RuntimeError(key+' conversion failure')
                    if key==init and case in ['eq-true','eq-false','eq-error','truth-error']:
                        return effects.Comparable(events,case.removeprefix('eq-') if case in ['eq-true','eq-false'] else case)
                    return baseconvert(key,value)
                def getter_registry(cls):events.append('getters');return registry()
                def clear_original(obj):
                    core.Model.clear_dirty(obj);state.append(dict(dirty=len(obj._dirty),revision=obj.__dict__.get('_revision')))
                def clear_candidate(local):
                    clear(local);state.append(dict(dirty=len(local['_dirty']),revision=local.get('_revision')))
                with ExitStack() as stack:
                    stack.enter_context(patch.object(Item,'_convert',classmethod(conversion)))
                    stack.enter_context(patch.object(Item,'_getters',classmethod(getter_registry)))
                    if arm=='original':stack.enter_context(patch.object(Item,'clear_dirty',clear_original))
                    result=outcome(lambda:original(rows,flex,resource,fields)) if arm=='original' else outcome(lambda:candidate(rows,flex,resource,fields,clear_candidate)[0])
                arms.append(dict(result=result,events=events,state=state))
            check('ordered effects '+case,arms[0]==arms[1]);effect_results.append(dict(case=case,arms=arms))
        save(out/'effect-cases.json',effect_results)
        # Preflight rejects known computed demands before executing a prefix.
        try:consume(core,Item,rows,flex,lib,lambda load:(_ for _ in ()).throw(AssertionError('prefix ran')),clear,[target],demand,getters={target:lambda x:1})
        except Unsupported:check('known computed consumer refused before effects',True)
        else:raise AssertionError('Computed demand admitted')
        with patch.object(Item,'__init__',side_effect=AssertionError('Item constructed')),patch.object(core.LazyDict,'__init__',side_effect=AssertionError('LazyDict constructed')):
            actual=candidate(rows,flex,lib,[target])
        check('candidate constructs neither Item nor LazyDict',actual[0]==original(rows,flex,lib,[target]))
        check('raw fields remain complete',len(actual[2][0])==len(Item._fields))
        check('full schema and all five tables unchanged',snapshot(dbpath)==initial and len(initial['tables'])==5)
        check('baseline file bytes unchanged',sha(base/'100/library.db')==original_hash)
        lib._close()
        save(out/'database-state.json',dict(before=initial,after=snapshot(dbpath)))
        report=dict(status='pass',checks=checks,component_pairs=sum(x['cases'] for x in summaries),
            application_rows=len(rows),decode_cases=len(numeric),effect_cases=len(effect_results),
            mutation_counterexamples=len(mutants),unsupported_structures=len(rejects),
            original_item_and_lazy_lookup_used=True,candidate_item_and_lazy_wrappers_absent=True,
            whole_application_extraction=False,whole_constructor_proof=False,scalar_ir_extended=False,
            backend_admitted=False,performance_measured=False,
            remaining=['Generic lazy-state primitive not yet integrated into scalar IR/checker',
                       'beets constructor/field-access composition remains an explicit development adapter',
                       'Private-state nonescape, stable dispatch, plugin exclusion and callback access assumptions are premises'])
        save(out/'summary.json',report)
        print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
    except BaseException as e:
        save(out/'failure.json',dict(type=type(e).__name__,message=str(e),checks=checks));raise
    finally:
        provenance={str(p.relative_to(root)):sha(p) for p in files}
        save(out/'source-manifest.json',provenance)
        for path in files:
            destination=out/'inputs'/path.relative_to(root)
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
        if 'bound' in locals():
            for i,witness in enumerate(bound.witness['source']):
                shutil.copyfile(witness['file'],out/('bound-source-'+str(i)+'.py'))
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
