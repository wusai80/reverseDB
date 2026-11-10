#!/usr/bin/env python3
"""Fixed complete-export integration of the recovered initialization effects.

Seven untimed exports: original/old/restored on the normal fixture, then
original/restored for multidisc and slow-sort fallbacks. No backend admission.
"""
import ast
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from initialization_effects import recover_empty_mapping_prefix, recover_private_state_step
from region_contracts import fingerprint, select_existing_binding
from test_beets_feasibility import snapshot, encode

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'work/beets/source-e978874d'
BASE=ROOT/'evidence/beets-feasibility/20261108-a03fafe1c20f'
SCALAR=ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'
PREFIX=ROOT/'evidence/initialization-effects/20261109-d12bba955bf8'
COMMON=ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
sys.path.insert(0,str(ROOT/'prototype/beets'))
from prefix_consumer import consume_packet

SCHEDULE=[dict(case=case,arm=arm) for case,arms in [
    ('normal',['original','old','restored']),
    ('multidisc',['original','restored']),
    ('slow-sort',['original','restored'])] for arm in arms]


def main():
    out=ROOT/'evidence/beets-prefix-export'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    checks=[];runs=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    def compile_code(code,name,filename,bindings):
        (out/filename).write_text(code)
        ns=dict(bindings);exec(compile(code,str(out/filename),'exec'),ns)
        return ns[name]
    save('protocol.json',dict(schedule=SCHEDULE,items=100,seed=20261109,
        entry='Original ExportPlugin.run, JSON library export, all default fields, id+ order, real media files',
        normal='Album tracks have tracktotal=17, not observed track count; entire prior fixture retained',
        multidisc='Album 1 has two discs with metadata 4 and 6; existing original-method fallback retained',
        slow_sort='Original rating_hint- slow sort for all album lookups; existing fallback retained',
        changes='Only replace final decode in the archived candidate with the previously tested source-prefix packet consumer',
        observes=['complete JSON bytes and all row/field values','full schema and five-table contents',
                  'ordered SQL and parameters','Item conversion inputs/order','private-state step completion',
                  'per-call fallback and model materialization counts','resident connection and context depth'],
        domain='Pinned no-plugin direct-export deployment, successful original reads, same prior read-deletion premise',
        limits=['Source-bound experiment, not independent binary decompilation',
                'No whole-constructor/receiver-identity proof or read-error preservation claim',
                'No new automatic backend admission, default application replacement or timing'],
        stop='One fixed seven-export integration; no performance-driven retry or additional parameter search'))
    for p in [Path(__file__),ROOT/'scripts/initialization_effects.py',ROOT/'prototype/beets/prefix_consumer.py',ROOT/'scripts/test_beets_feasibility.py']:
        shutil.copy2(p,out/p.name)
    try:
        check('explicit fixed hash seed',os.environ.get('PYTHONHASHSEED')=='20261109')
        for label,archive in [('scalar',SCALAR),('prefix',PREFIX),('common',COMMON)]:
            check(label+' frozen evidence intact',all(fingerprint(archive/p)==h for p,h in load(archive/'manifest.json').items()))
        for name in ['initialization_effects.py','prefix_consumer.py']:
            check(name+' identical to tested prefix implementation',fingerprint(out/name)==fingerprint(PREFIX/name))
        source_manifest=load(BASE/'source-manifest.json')
        check('entire pinned upstream source intact',all(fingerprint(SOURCE/p)==h for p,h in source_manifest.items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged',fingerprint(ROOT/'scripts'/f)==fingerprint(FIRST/'inputs/scripts'/f))
        for name in ['neworder','payment','beets']:
            check(name+' complete existing decision unchanged',select_existing_binding(load(COMMON/(name+'-description.json')),ROOT)==load(COMMON/(name+'-decision.json')))
        media={str(p.relative_to(ROOT)):fingerprint(p) for p in sorted((BASE/'100/music').glob('*.wav'))}
        check('all 100 original media files intact',len(media)==100 and all(h==load(BASE/'manifest.json')[str((ROOT/p).relative_to(BASE))] for p,h in media.items()))
        save('media-manifest.json',media)
        input_paths=[BASE/'100/library.db',BASE/'source-manifest.json',SCALAR/'probe.py',SCALAR/'drop-flex-and-model.py',
                     SCALAR/'manifest.json',PREFIX/'manifest.json',COMMON/'manifest.json']
        save('input-hashes.json',{str(p.relative_to(ROOT)):fingerprint(p) for p in input_paths})
        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n')
        os.environ['BEETSDIR']=str(out/'config');sys.path.insert(0,str(SOURCE))
        import beets
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Item,Album,Library
        from beetsplug.export import ExportPlugin
        beets.config['plugins']=[];plugin=ExportPlugin();default_sort=beets.config['sort_item'].get()
        plan=recover_empty_mapping_prefix(Item,'update');state_plan=recover_private_state_step(Item,'clear_dirty')
        check('recovered plans match the tested source plans',plan==load(PREFIX/'prefix-plan.json') and state_plan==load(PREFIX/'state-plan.json'))
        prefix=compile_code(plan['code'],'run_initialization_prefix','prefix.py',{})
        clear=compile_code(state_plan['code'],'run_private_state_step','state.py',{})
        reader=compile_code((SCALAR/'drop-flex-and-model.py').read_text(),'read_fixed_rows','reader.py',core.__dict__)
        fn=next(n for n in ast.walk(ast.parse((SCALAR/'probe.py').read_text())) if isinstance(n,ast.FunctionDef) and n.name=='candidate')
        original_fn=copy.deepcopy(fn);terminal=fn.body[-1]
        # Bind the actual archived output demand, rather than introducing a new
        # hand-coded field rule or rewriting the reader / fallback conditions.
        check('expected archived terminal decode shape',isinstance(terminal,ast.Return) and isinstance(terminal.value,ast.Call)
              and ast.unparse(terminal.value.func)=='Item._convert' and len(terminal.value.args)==2 and not terminal.value.keywords)
        field=terminal.value.args[0]
        check('same literal field used in converter and row access',isinstance(field,ast.Constant) and isinstance(field.value,str)
              and ast.unparse(terminal.value.args[1])==f'rows[0][{field.value!r}]')
        replacement=ast.parse("return consume_packet(core, Item, rows, [], album._db, prefix, clear_state, [FIELD])[0][0]").body[0]
        replacement.value.value.value.args[-1].elts[0]=copy.deepcopy(field)
        fn.body[-1]=replacement;ast.fix_missing_locations(fn)
        check('all reader calls and existing guards unchanged',ast.dump(ast.Module(body=fn.body[:-1],type_ignores=[]),include_attributes=False)==
              ast.dump(ast.Module(body=original_fn.body[:-1],type_ignores=[]),include_attributes=False))
        check('fixed native fields and constructor',not list(plugins.find_plugins()) and Item.__init__ is core.Model.__init__ and
              all(k in Item._fields and k not in Item._getters() for k in plan['field_reads']+[field.value]))
        save('binding.json',dict(prefix_plan=plan,state_plan=state_plan,output_field=field.value,
             replaced_ast=ast.dump(terminal,include_attributes=False),replacement_ast=ast.dump(fn.body[-1],include_attributes=False),
             admitted=False,flex_values='Empty only under the prior successful-read fixed-deployment deletion premise'))
        save('runtime.json',dict(python=sys.version,sqlite=sqlite3.sqlite_version,beets=beets.__version__,source=beets.__file__,default_sort=default_sort))
        original_total=Album._albumtotal;original_query=core.Transaction.query
        original_model=core.Results._make_model;original_convert=Item._convert;original_clear=Item.clear_dirty
        fallbacks=[];context={'scope':None};events=[]
        def depth(lib):
            with lib._tx_stack() as stack:return len(stack)
        def state_event(local):
            events.append(dict(kind='state',scope=context['scope'],dirty=len(local['_dirty']),
                               revision=local.get('_revision'),depth=depth(local['_db'])))
        def clear_candidate(local):clear(local);state_event(local)
        bindings=dict(original=original_total,beets=beets,Item=Item,query=query,fallbacks=fallbacks,
                      readers={'drop-flex-and-model':reader},consume_packet=consume_packet,core=core,prefix=prefix,clear_state=clear_candidate)
        old=compile_code(ast.unparse(original_fn)+'\n','candidate','old-candidate.py',bindings)
        restored=compile_code(ast.unparse(fn)+'\n','candidate','restored-candidate.py',bindings)
        # Templates differ only in the declared metadata cells; no rows removed.
        baseline=snapshot(BASE/'100/library.db');templates={}
        for case in ['normal','multidisc','slow-sort']:
            path=out/(case+'-template.db')
            with sqlite3.connect(BASE/'100/library.db') as src,sqlite3.connect(path) as dst:
                src.backup(dst);dst.execute('UPDATE items SET tracktotal=17 WHERE album_id IS NOT NULL')
                if case=='multidisc':
                    dst.execute('UPDATE albums SET disctotal=2 WHERE id=1')
                    dst.execute('UPDATE items SET disc=CASE WHEN track<=5 THEN 1 ELSE 2 END, tracktotal=CASE WHEN track<=5 THEN 4 ELSE 6 END WHERE album_id=1')
            templates[case]=snapshot(path);save(case+'-template-state.json',templates[case])
            check(case+' all tables and row counts retained',templates[case]['schema']==baseline['schema'] and
                  {t:len(v) for t,v in templates[case]['tables'].items()}=={t:len(v) for t,v in baseline['tables'].items()})
        for trial in SCHEDULE:
            case,arm=trial['case'],trial['arm'];label=case+'/'+arm;folder=out/label;folder.mkdir(parents=True)
            path=folder/'library.db'
            with sqlite3.connect(out/(case+'-template.db')) as src,sqlite3.connect(path) as dst:src.backup(dst)
            lib=Library(path,directory=BASE/'100/music');connection=lib._connection()
            check(label+' complete initial state',snapshot(path)==templates[case])
            beets.config['per_disc_numbering']=case=='multidisc'
            beets.config['sort_item']=['rating_hint-'] if case=='slow-sort' else default_sort
            events=[];fallbacks.clear();context['scope']=None;calls=[];models={'Item':0,'Album':0}
            def observed_query(tx,sql,params=()):
                values=original_query(tx,sql,params)
                events.append(dict(kind='query',scope=context['scope'],sql=sql,params=encode(list(params)),rows=len(values)))
                return values
            def observed_convert(cls,key,value):
                events.append(dict(kind='convert',scope=context['scope'],field=key,value=encode(value),depth=depth(lib)))
                return original_convert(key,value)
            def observed_clear(item):
                original_clear(item);state_event(item.__dict__)
            def observed_model(result,*args,**kwargs):
                models[result.model_class.__name__]+=1
                return original_model(result,*args,**kwargs)
            def observed_total(album):
                scope=len(calls);call={'scope':scope};calls.append(call)
                previous=context['scope'];context['scope']=scope;start=len(fallbacks)
                events.append(dict(kind='enter',scope=scope))
                try:
                    value=original_total(album) if arm=='original' else (old if arm=='old' else restored)(album,'drop-flex-and-model')
                    events.append(dict(kind='exit',scope=scope,value=encode(value)))
                    call['value']=encode(value)
                    return value
                finally:
                    call['fallbacks']=fallbacks[start:];context['scope']=previous
            opts=SimpleNamespace(output=str(folder/'output.json'),append=False,format='json',library=True,album=False,included_keys=[],limit=None)
            try:
                with ExitStack() as stack:
                    stack.enter_context(patch.object(core.Transaction,'query',observed_query))
                    stack.enter_context(patch.object(Item,'_convert',classmethod(observed_convert)))
                    stack.enter_context(patch.object(Item,'clear_dirty',observed_clear))
                    stack.enter_context(patch.object(core.Results,'_make_model',observed_model))
                    stack.enter_context(patch.object(Album,'_albumtotal',observed_total))
                    plugin.run(lib,opts,['id+'])
                connection_retained=lib._connection() is connection
                check(label+' resident connection retained',connection_retained)
            finally:
                save(label+'/events.json',events);save(label+'/calls.json',calls)
                lib._close()
            check(label+' original entry restored',Album._albumtotal is original_total and Item.clear_dirty is original_clear)
            check(label+' complete database state retained',snapshot(path)==templates[case])
            data=load(folder/'output.json')
            check(label+' complete 100 rows and default fields',len(data)==100 and [int(x['id']) for x in data]==list(range(1,101)) and
                  all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=x.keys() for x in data))
            runs.append(dict(**trial,queries=sum(e['kind']=='query' for e in events),models=models,calls=len(calls),connection_retained=connection_retained,
                             conversions=sum(e['kind']=='convert' for e in events),state_steps=sum(e['kind']=='state' for e in events),
                             output_sha256=fingerprint(folder/'output.json'),output_bytes=(folder/'output.json').stat().st_size))
            save('runs.json',runs)
            print(label+' complete',flush=True)
        comparisons=[]
        for case in ['normal','multidisc','slow-sort']:
            a=load(out/case/'original/events.json');b=load(out/case/'restored/events.json')
            calls=load(out/case/'restored/calls.json')
            scopes={c['scope'] for c in calls if not c['fallbacks']}
            expected=[];removed=[]
            for event in a:
                # Identify the original second query per optimized invocation,
                # not a global text filter that could hide unrelated queries.
                scope=event['scope'];queries=[e for e in a if e['kind']=='query' and e['scope']==scope] if event['kind']=='query' and scope in scopes else []
                if queries and event is queries[-1]:
                    check(case+'/'+str(scope)+' exact original two-read region',len(queries)==2 and 'FROM item_attributes' in event['sql'] and 'album_id' in event['sql'])
                    removed.append(event)
                else:expected.append(event)
            save(case+'/expected-retained-events.json',expected)
            check(case+' full observable order except declared flex reads',expected==b)
            check(case+' all complete output bytes identical',(out/case/'original/output.json').read_bytes()==(out/case/'restored/output.json').read_bytes())
            original_calls=load(out/case/'original/calls.json')
            check(case+' all invocation results identical',[(c['scope'],c['value']) for c in original_calls]==[(c['scope'],c['value']) for c in calls])
            # The retained fixture has disctotal=0 for the other albums, so
            # per-disc numbering sends every album through the original path.
            expected_removed={'normal':90,'multidisc':0,'slow-sort':0}[case]
            check(case+' declared reduction and fallback counts',len(removed)==len(scopes)==expected_removed and len(calls)==90)
            check(case+' only original fallback reasons',all(c['fallbacks']==([] if c['scope'] in scopes else ['multidisc' if case=='multidisc' else 'slow-sort']) for c in calls))
            comparisons.append(dict(case=case,removed_queries=removed,optimized_calls=len(scopes),fallback_calls=len(calls)-len(scopes),events_equal_after_declared_deletion=True))
        save('comparisons.json',comparisons)
        normal={r['arm']:r for r in runs if r['case']=='normal'}
        check('old and restored normal output still identical',normal['original']['output_sha256']==normal['old']['output_sha256']==normal['restored']['output_sha256'])
        check('actual normal query/materialization reduction retained',normal['original']['queries']==362 and normal['restored']['queries']==normal['old']['queries']==272 and
              sum(normal['original']['models'].values())==280 and sum(normal['restored']['models'].values())==sum(normal['old']['models'].values())==190)
        old_events=load(out/'normal/old/events.json');a=load(out/'normal/original/events.json');b=load(out/'normal/restored/events.json')
        no_queries=lambda events:[e for e in events if e['kind']!='query']
        check('old candidate missing effects actually reproduced',no_queries(old_events)!=no_queries(a) and no_queries(b)==no_queries(a))
        check('restored exactly 90 initialization conversions and state steps',normal['restored']['conversions']-normal['old']['conversions']==90 and normal['restored']['state_steps']-normal['old']['state_steps']==90)
        check('exact seven-export schedule',[(r['case'],r['arm']) for r in runs]==[(r['case'],r['arm']) for r in SCHEDULE])
        check('all original media bytes unchanged',all(fingerprint(ROOT/p)==h for p,h in media.items()))
        check('all evidence inputs unchanged',all(fingerprint(ROOT/p)==h for p,h in load(out/'input-hashes.json').items()))
        check('pinned application source untouched',all(fingerprint(SOURCE/p)==h for p,h in source_manifest.items()))
        save('report.json',dict(status='passed',checks=checks,executions=len(runs),runs=runs,
            result='Recovered initialization effects integrated into the existing candidate; complete default exports and original fallbacks match',
            performance_measured=False,production_changed=False,shared_backend_admitted=False,
            limits='Fixed successful-read source-bound deployment only; original SQL read-error/receiver-identity boundaries remain unresolved'))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks,completed=len(runs)));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out,flush=True)


if __name__=='__main__':main()
