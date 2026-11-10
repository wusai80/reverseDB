"""Source extraction attempt, exceptional-flow counterexamples and safe selection."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch

from .source_region import extract,select_entry
from .checked_ir import catalog_record,propose,compile_program
from .fixtures import RenamedLazy,ExtraEffect
from .lowering import Unsupported
from .verify_checked_ir import beets_program


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):p.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')


class ConversionKeyError(KeyError):pass


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    root=Path.cwd();out=args.out;out.mkdir(parents=True,exist_ok=False);checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    files=[root/'prototype/lazy_state/source_region.py',Path(__file__).resolve(),root/'prototype/lazy_state/verify_checked_ir.py']
    try:
        prior=root/'paper/checked-lazy-ir-freeze.json';frozen=json.loads(prior.read_text())
        check('previous freeze unchanged',all(sha(root/p)==h for p,h in frozen['sha256'].items()))
        sys.path.insert(0,str(root/'scripts'));source=root/'work/beets/source-e978874d';sys.path.insert(0,str(source))
        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n');os.environ['BEETSDIR']=str((out/'config').resolve())
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Item,Library
        from initialization_effects import recover_empty_mapping_prefix,recover_private_state_step
        from test_backend_boundaries import value_repr
        from test_beets_feasibility import snapshot
        base=root/'evidence/beets-feasibility/20261108-a03fafe1c20f'
        check('pinned source unchanged',all(sha(source/p)==h for p,h in json.loads((base/'source-manifest.json').read_text()).items()))
        check('no plugins',not list(plugins.find_plugins()))
        save(out/'protocol.json',dict(goal='Attempt source-derived region admission, retain unresolved exception continuations',
            full_model='Original Item constructor plus attribute consumer',component='Complete source-checked lazy-map protocol',
            cases=['normal','target-runtime','target-key','init-key','target-key-subclass'],
            no_performance=True,no_new_project=True,no_default_patch=True,
            fault_injection='Converter exceptions deliberately exercise paths outside the earlier successful/native adapter domain'))
        target='tracktotal';decision=extract(Item,[target]);save(out/'item-source-decision.json',decision)
        check('whole model not admitted',not decision['admitted'] and decision['program'] is None and decision['selected']=='original')
        check('constructor statements recovered in order',len(decision['constructor_steps'])==6)
        boundaries=decision['source_graph']['boundaries'];edges=decision['source_graph']['edges']
        check('exception continuations found',any(x['kind']=='exception_continuation' and x['catches']=='KeyError' for x in boundaries))
        check('property dependency found',any(x['kind']=='property_dependency' for x in edges))
        check('resource callback dependency found',any(x['kind']=='resource_call' for x in boundaries))
        # A closed existing component can be extracted and passed to the same checker.
        component_results=[]
        for cls in [core.LazyDict,RenamedLazy]:
            d=extract(cls,['a','a'],access='item');check(cls.__qualname__+' source extraction admitted',d['admitted'])
            events=[]
            def converter(k,v):events.append(k);return [k,v]
            resources=dict(bindings={'lazy':cls},raws={'raw':{'a':4,'unused':19}},converters={'convert':converter},calls={})
            def factory(program):
                cat=catalog_record(**resources);q,c=propose(program,cat);fn,code,verdict=compile_program(program,q,c,cat,**resources)
                return lambda:fn(None,{})
            entry=select_entry(cls,['a','a'],d,lambda:None,factory,access='item');result=entry()
            check(cls.__qualname__+' cache identity and complete data',result[0] is result[1] and events==['a'] and resources['raws']['raw']=={'a':4,'unused':19})
            component_results.append(dict(decision=d,events=events,result=result,identity=result[0] is result[1]))
        save(out/'component-extractions.json',component_results)
        changed=extract(ExtraEffect,['a'],access='item');check('extra lookup effect refused',not changed['admitted'])
        forged=deepcopy(decision);forged['admitted']=True
        try:select_entry(Item,[target],forged,lambda:None,lambda p:None)
        except Unsupported:check('forged source decision rejected',True)
        else:raise AssertionError('Forged admission')
        with patch.object(Item,'__getitem__',RenamedLazy.__getitem__):
            try:select_entry(Item,[target],decision,lambda:None,lambda p:None)
            except Unsupported:check('source dispatch change rejected',True)
            else:raise AssertionError('Stale source decision')
        path=out/'library.db';baseline_hash=sha(base/'100/library.db')
        with sqlite3.connect(base/'100/library.db') as src,sqlite3.connect(path) as dst:src.backup(dst)
        lib=Library(path,directory=base/'100/music');before=snapshot(path)
        packet=lib.items(query.MatchQuery('album_id',1));rows,flex=list(packet.rows),list(packet.flex_rows)
        fixed=dict(rows[0]);indexed=core.Results(Item,list(rows),lib,list(flex))._get_indexed_flex_attrs()
        prefix_plan=recover_empty_mapping_prefix(Item,'update');state_plan=recover_private_state_step(Item,'clear_dirty')
        init=prefix_plan['field_reads'][0]
        ns={};exec(prefix_plan['code'],ns);prefix=ns['run_initialization_prefix']
        ns={};exec(state_plan['code'],ns);clear=ns['run_private_state_step']
        baseconvert=Item._convert;cases=[]
        for case in ['normal','target-runtime','target-key','init-key','target-key-subclass']:
            arms=[]
            for arm in ['original','previous-candidate','source-selected']:
                events=[];sql=[];counts=dict(original=0,candidate_factory=0)
                def convert(cls,key,value):
                    events.append(['convert',key])
                    if case=='init-key' and key==init:raise KeyError('conversion fault:'+key)
                    if key==target:
                        if case=='target-runtime':raise RuntimeError('conversion fault:'+key)
                        if case=='target-key':raise KeyError('conversion fault:'+key)
                        if case=='target-key-subclass':raise ConversionKeyError('conversion fault:'+key)
                    return baseconvert(key,value)
                def original():
                    counts['original']+=1
                    obj=core.Results(Item,list(rows),lib,list(flex))[0]
                    return (getattr(obj,target),)
                def old_candidate():
                    state={'_db':lib,'_dirty':set()}
                    resources=dict(bindings={'lazy':core.LazyDict},raws={'fixed':dict(fixed),'flexible':indexed.get(fixed['id'],{}) or {}},
                        converters={'convert':Item._convert},calls={'registry':lambda:Item._getters(),'prefix':lambda v:prefix(lambda key:v),'clear':lambda:clear(state)})
                    p=beets_program(init,[target]);cat=catalog_record(**resources);q,c=propose(p,cat)
                    fn,code,admission=compile_program(p,q,c,cat,**resources)
                    return lambda:fn(None,{})
                def forbidden_factory(program):
                    counts['candidate_factory']+=1
                    raise AssertionError('Unsupported model candidate factory executed')
                with patch.object(Item,'_convert',classmethod(convert)):
                    entry=original if arm=='original' else old_candidate() if arm=='previous-candidate' else select_entry(Item,[target],decision,original,forbidden_factory)
                    lib._connection().set_trace_callback(sql.append)
                    try:result=dict(values=[value_repr(x) for x in entry()])
                    except Exception as error:result=dict(error=type(error).__name__,message=str(error))
                    finally:lib._connection().set_trace_callback(None)
                arms.append(dict(arm=arm,outcome=result,events=events,sql=sql,counts=counts))
            a,b,c=arms
            check(case+' guarded original outcome/effects/queries',all(a[k]==c[k] for k in ['outcome','events','sql']))
            check(case+' no candidate construction or replay',c['counts']==dict(original=1,candidate_factory=0))
            differs=a['outcome']!=b['outcome']
            check(case+' expected exception-boundary finding',differs==(case in ['target-key','init-key','target-key-subclass']))
            if differs:check(case+' fallback SQL retained only by original path',len(a['sql'])>len(b['sql']))
            cases.append(dict(case=case,arms=arms,previous_candidate_differs=differs))
        save(out/'exception-counterexamples.json',cases)
        check('all five tables schema and baseline unchanged',snapshot(path)==before and len(before['tables'])==5 and sha(base/'100/library.db')==baseline_hash)
        lib._close();save(out/'database-state.json',dict(before=before,after=snapshot(path)))
        report=dict(status='pass',checks=checks,source_nodes=len(decision['source_graph']['nodes']),source_edges=len(edges),
            model_admitted=False,complete_component_extractions=len(component_results),exception_counterexamples=sum(x['previous_candidate_differs'] for x in cases),
            guarded_original_cases=len(cases),private_cache_ir_reused=True,full_source_to_IR_gap_closed=False,
            performance_measured=False,reason='Opaque conversion can enter receiver-dependent exception continuations; they cannot be omitted from source-to-IR extraction')
        save(out/'summary.json',report);print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
    except BaseException as error:
        save(out/'failure.json',dict(type=type(error).__name__,message=str(error),checks=checks));raise
    finally:
        for p in files:
            dst=out/'inputs'/p.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
        save(out/'source-manifest.json',{str(p.relative_to(root)):sha(p) for p in files})
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
