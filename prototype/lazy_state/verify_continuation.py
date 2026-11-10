"""One fixed source-continuation/reification study on development data."""
import argparse
from copy import deepcopy
from contextlib import ExitStack
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch

from .continuation import lift_handler
from .continuation_region import bind_region,check_record,Region
from .continuation_fixtures import RenamedContinuation,BareReraise,FinallyBoundary,RebindingReceiver,ChangedDelegate,RecoveryAlbum,ReceiverDescriptor
from .lowering import Unsupported


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True,allow_nan=False)+'\n')


class ConversionKeyError(KeyError):pass


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    root=Path.cwd();out=args.out;out.mkdir(parents=True,exist_ok=False);checks=[]
    def check(n,ok):
        if not ok:raise AssertionError(n)
        checks.append(n)
    files=[root/'prototype/lazy_state'/n for n in ['continuation.py','continuation_region.py','continuation_fixtures.py','verify_continuation.py']]
    try:
        for name in ['source-region-freeze.json','checked-lazy-ir-freeze.json']:
            freeze=json.loads((root/'paper'/name).read_text());check(name+' unchanged',all(sha(root/p)==h for p,h in freeze['sha256'].items()))
        controls=[]
        for cls in [RenamedContinuation,BareReraise]:
            lifted,record=lift_handler(cls,'__getitem__')
            for failure in [False,True]:
                arms=[]
                for arm in ['original','lifted']:
                    events=[];error=KeyError('original error');obj=cls();attempts=[];materializations=[]
                    def primary(key):
                        attempts.append(key)
                        if failure:raise error
                        return 13
                    obj.primary=primary;obj.backup=lambda key:events.append(['backup',key]) or 21;obj.observe=lambda:events.append('observe')
                    def receiver():materializations.append(True);return obj
                    try:result=dict(value=obj['field'] if arm=='original' else lifted(lambda:primary('field'),receiver,'field'))
                    except Exception as exc:result=dict(error=type(exc).__name__,message=str(exc),same_exception=exc is error)
                    arms.append(dict(result=result,events=events,attempts=attempts,materializations=len(materializations)))
                check(cls.__name__+'/'+str(failure)+' continuation equivalent',all(arms[0][k]==arms[1][k] for k in ['result','events','attempts']) and len(arms[1]['attempts'])==1)
                check('no eager receiver '+cls.__name__+'/'+str(failure),arms[1]['materializations']==int(failure))
                controls.append(dict(class_name=cls.__name__,failure=failure,arms=arms,source=record))
        rejects=[]
        for cls in [FinallyBoundary,RebindingReceiver,ChangedDelegate]:
            try:lift_handler(cls,'__getitem__')
            except Unsupported as e:rejects.append(dict(class_name=cls.__name__,reason=str(e)))
            else:raise AssertionError('Unsupported handler accepted')
        save(out/'generic-controls.json',controls);save(out/'rejections.json',rejects)
        sys.path.insert(0,str(root/'scripts'));source=root/'work/beets/source-e978874d';sys.path.insert(0,str(source))
        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n');os.environ['BEETSDIR']=str((out/'config').resolve())
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Item,Library
        from test_backend_boundaries import VALUES,value_repr
        from test_beets_feasibility import snapshot
        base=root/'evidence/beets-feasibility/20261108-a03fafe1c20f'
        check('pinned application source unchanged',all(sha(source/p)==h for p,h in json.loads((base/'source-manifest.json').read_text()).items()))
        check('no plugins',not list(plugins.find_plugins()))
        binding=bind_region(Item);save(out/'source-binding.json',binding[-1])
        check('source record re-extraction accepted',check_record(Item,binding[-1])['accepted'])
        forged=deepcopy(binding[-1]);forged['item_handler']['code']='def resume(*args): return None\n'
        check('forged continuation code refused',not check_record(Item,forged)['accepted'])
        (out/'item-handler.py').write_text(binding[-1]['item_handler']['code']);(out/'attribute-handler.py').write_text(binding[-1]['attribute_handler']['code'])
        (out/'native-continuation.py').write_text(binding[-1]['native_lookup']['resume_code'])
        with patch.object(Item,binding[2]['fields'],ReceiverDescriptor()):
            try:bind_region(Item)
            except Unsupported:check('receiver-dependent metadata descriptor refused',True)
            else:raise AssertionError('Receiver metadata admitted')
        save(out/'protocol.json',dict(goal='Preserve source exception continuations without repeating a failed lookup or restarting construction',
            ordinary_rows=100,numeric_cases=17,component_controls=4,
            modes=['normal','target-runtime','target-key','init-key','target-key-subclass','album-success','one-shot-album','one-shot-init-recovery','callback-error','getter-key','getter-error','prefix-key','cached-object-reification'],
            no_performance=True,no_default_patch=True,no_new_IR_admission=True))
        path=out/'library.db';base_hash=sha(base/'100/library.db')
        with sqlite3.connect(base/'100/library.db') as a,sqlite3.connect(path) as b:a.backup(b)
        lib=Library(path,directory=base/'100/music');before=snapshot(path)
        packet=lib.items();all_rows,flex=list(packet.rows),list(packet.flex_rows)
        indexed=core.Results(Item,list(all_rows),lib,list(flex))._get_indexed_flex_attrs()
        row=next(r for r in all_rows if r['album_id']==1)
        parameters=[v['parameter'] for v in binding[1]['lazy'].values()]
        init=binding[-1]['prefix']['field_reads'][0];target='tracktotal'
        def region(row):return Region(binding,lib,{parameters[0]:dict(row),parameters[1]:indexed.get(row['id'],{}) or {}})
        def outcome(fn):
            try:
                result=fn();return dict(values=[value_repr(v) for v in result]),result
            except Exception as e:
                context=e.__context__
                return dict(error=type(e).__name__,message=str(e),context=None if context is None else dict(type=type(context).__name__,message=str(context))),None
        ordinary=[]
        for r in all_rows:
            obj=core.Results(Item,[r],lib,list(flex))[0];a,_=outcome(lambda:tuple(getattr(obj,k) for k in ['id',target,init,target]))
            candidate=region(r);b,_=outcome(lambda:candidate.run(['id',target,init,target]))
            check('row '+str(r['id']),a==b and candidate.virtual.materializations==0)
            ordinary.append(dict(id=r['id'],outcome=a,materializations=0))
        save(out/'all-rows.json',ordinary)
        numeric=[]
        for name,value in VALUES:
            if name=='empty':continue
            r=dict(row,**{target:value})
            a,_=outcome(lambda:tuple(getattr(core.Results(Item,[r],lib,list(flex))[0],k) for k in [target]))
            candidate=region(r);b,_=outcome(lambda:candidate.run([target]))
            check('decode '+name,a==b);numeric.append(dict(case=name,original=a,candidate=b,materializations=candidate.virtual.materializations))
        save(out/'decode-cases.json',numeric)
        baseconvert=Item._convert;basegetters=Item._getters;original_get_album=lib.get_album
        modes=['normal','target-runtime','target-key','init-key','target-key-subclass','album-success','one-shot-album','one-shot-init-recovery','callback-error','getter-key','getter-error','prefix-key','cached-object-reification']
        cases=[]
        for mode in modes:
            records=[]
            for arm in ['original','candidate']:
                events=[];sql=[];attempts={};created=[];candidate=None;receiver=None
                fields=['album','album',target] if mode in ['album-success','one-shot-album'] else [target,'album',target] if mode=='cached-object-reification' else [init,init,target] if mode=='one-shot-init-recovery' else [target]
                def conversion(cls,key,value):
                    attempts[key]=attempts.get(key,0)+1;events.append(['convert',key,attempts[key]])
                    if key==target and mode=='target-runtime':raise RuntimeError('conversion fault:'+key)
                    if key==target and mode in ['target-key','target-key-subclass','callback-error']:
                        raise (ConversionKeyError if mode=='target-key-subclass' else KeyError)('conversion fault:'+key)
                    if key==init and (mode=='init-key' or mode=='one-shot-init-recovery' and attempts[key]==1):raise KeyError('conversion fault:'+key)
                    if key=='album' and (mode in ['album-success','cached-object-reification'] or mode=='one-shot-album' and attempts[key]==1):raise KeyError('conversion fault:'+key)
                    if mode=='cached-object-reification' and key==target:return ['cached',value]
                    if mode=='prefix-key' and key==init:
                        class Predicate:
                            def __eq__(self,other):events.append('prefix_eq');raise KeyError('predicate failure')
                        return Predicate()
                    return baseconvert(key,value)
                def getter(obj):
                    events.append(['getter',type(obj).__name__])
                    if mode=='getter-key':raise KeyError('computed failure')
                    raise RuntimeError('computed failure')
                def registry(cls):
                    events.append('registry');result=basegetters()
                    return dict(result,**{target:getter}) if mode.startswith('getter-') else result
                fake=RecoveryAlbum(events)
                def get_album(obj):
                    events.append(['get_album',type(obj).__name__])
                    if mode=='callback-error':raise RuntimeError('album callback failure')
                    if mode=='one-shot-init-recovery':return fake
                    return original_get_album(obj)
                # Observe original constructor calls without changing extracted binding.
                original_init=Item.__init__
                def init_observer(obj,*args,**kwargs):created.append(obj);return original_init(obj,*args,**kwargs)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(Item,'_convert',classmethod(conversion)))
                    stack.enter_context(patch.object(Item,'_getters',classmethod(registry)))
                    stack.enter_context(patch.object(lib,'get_album',get_album))
                    stack.enter_context(patch.object(Item,'__init__',init_observer))
                    if arm=='candidate':candidate=region(row)
                    lib._connection().set_trace_callback(sql.append)
                    def execute():
                        nonlocal receiver
                        if arm=='candidate':return candidate.run(fields)
                        receiver=core.Results(Item,[row],lib,list(flex))[0]
                        return tuple(getattr(receiver,k) for k in fields)
                    result,values=outcome(execute)
                    lib._connection().set_trace_callback(None)
                if arm=='original' and receiver is None and created:receiver=created[0]
                state=receiver.__dict__ if arm=='original' else candidate.virtual.state
                slots=list(binding[1]['lazy']);memo={}
                for slot in slots:
                    if slot in state:cache=getattr(state[slot],binding[1]['lazy'][slot]['binding'].cache)
                    else:cache=candidate.virtual.lazy[slot][1]
                    memo[slot]=sorted(cache)
                aliases=None if values is None else [[x is y for y in values] for x in values]
                record=dict(outcome=result,events=events,sql=sql,attempts=attempts,cache_keys=memo,
                            dirty=len(state[binding[1]['fields']['dirty']]),revision=state.get('_revision'),
                            aliases=aliases,item_init_calls=len(created),materializations=None if candidate is None else candidate.virtual.materializations)
                records.append(record)
            a,b=records
            save(out/'cases-in-progress.json',cases+[dict(case=mode,arms=records)])
            for category in ['outcome','events','sql','attempts','cache_keys','dirty','revision','aliases']:
                check(mode+' '+category,a[category]==b[category])
            check(mode+' original init exactly once and candidate never restarts',a['item_init_calls']==1 and b['item_init_calls']==0)
            expected=0 if mode in ['normal','target-runtime','prefix-key'] else 1
            check(mode+' expected materialization count',b['materializations']==expected)
            cases.append(dict(case=mode,arms=records))
        save(out/'continuation-cases.json',cases)
        normal=region(row)
        with patch.object(Item,'__init__',side_effect=AssertionError('Item initialized')),patch.object(core.LazyDict,'__init__',side_effect=AssertionError('Lazy wrapper initialized')):
            normal.run([target])
        check('ordinary path constructs neither wrapper',normal.virtual.materializations==0)
        check('full data and baseline retained',snapshot(path)==before and len(before['tables'])==5 and sha(base/'100/library.db')==base_hash)
        lib._close();save(out/'database-state.json',dict(before=before,after=snapshot(path)))
        report=dict(status='pass',generic_controls=len(controls),unsupported_handler_forms=len(rejects),ordinary_rows=len(ordinary),decode_cases=len(numeric),continuation_cases=len(cases),
                    failed_operation_replayed=False,constructor_restarted=False,normal_path_materializations=0,
                    previous_three_counterexamples_resolved=True,admitted_to_frozen_IR=False,default_application_modified=False,performance_measured=False,checks=checks)
        save(out/'summary.json',report);print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
    except BaseException as error:
        save(out/'failure.json',dict(type=type(error).__name__,message=str(error),checks=checks));raise
    finally:
        for p in files:
            dst=out/'inputs'/p.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
        save(out/'source-manifest.json',{str(p.relative_to(root)):sha(p) for p in files})
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
