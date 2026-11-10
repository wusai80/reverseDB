#!/usr/bin/env python3
"""Fixed semantic ablations of initialization, predicate evaluation and private state."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch
from uuid import uuid4

from initialization_effects import recover_empty_mapping_prefix, recover_private_state_step, UnsupportedPrefix
from region_contracts import fingerprint, select_existing_binding
from test_beets_feasibility import snapshot
from test_backend_boundaries import VALUES, value_repr

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'work/beets/source-e978874d'
BASE=ROOT/'evidence/beets-feasibility/20261108-a03fafe1c20f'
COMMON=ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
sys.path.insert(0,str(ROOT/'prototype/beets'))
import prefix_fixtures as fixtures
from prefix_consumer import consume_packet

EFFECT_CASES=['normal','init-error','target-error','eq-true','eq-false','eq-error',
              'truth-error','resource-false','resource-truth-error','resource-revision-error',
              'cache-init','cache-target','getter-registry-effect']


def main():
    out=ROOT/'evidence/effect-ablation'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    checks=[]
    def check(n,v):
        assert v,n
        checks.append(n)
    def outcome(fn):
        try:return dict(values=[value_repr(v) for v in fn()])
        except Exception as exc:return dict(error=type(exc).__name__,message=str(exc))
    def compile_plan(plan,name,filename):
        (out/filename).write_text(plan['code']);ns={}
        exec(compile(plan['code'],str(out/filename),'exec'),ns)
        return ns[name]
    save('protocol.json',dict(seed=20261109,decode_cases=[n for n,v in VALUES],effect_cases=EFFECT_CASES,
        ablation_arms=['original','candidate','no_initialization','load_without_predicate','no_private_state'],
        generic_controls=['renamed method/field','inherited dispatch','effectful comparison/truth',
                          'renamed private state','unknown call','reachable branch','extra base effects',
                          'public state','resource write','receiver escape','external name','local rebinding','shadowed builtins'],
        full_data='Original 100-item/five-table fixture, both original queries and all row/flex fields retained',
        scope='Source-derived empty-kwargs update prefix and private clear_dirty step; fixed native field access adapter',
        non_goals=['whole-constructor proof','arbitrary receiver getters/identity','database-read deletion',
                   'backend admission','new selector','performance rerun'],
        stop='One fixed semantic experiment; keep source limitations explicit'))
    for p in [Path(__file__),ROOT/'scripts/initialization_effects.py',ROOT/'prototype/beets/prefix_consumer.py',ROOT/'prototype/beets/prefix_fixtures.py']:
        shutil.copy2(p,out/p.name)
    try:
        check('fixed hash seed',os.environ.get('PYTHONHASHSEED')=='20261109')
        check('whole pinned source intact',all(fingerprint(SOURCE/p)==h for p,h in load(BASE/'source-manifest.json').items()))
        check('common evidence intact',all(fingerprint(COMMON/p)==h for p,h in load(COMMON/'manifest.json').items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged',fingerprint(ROOT/'scripts'/f)==fingerprint(FIRST/'inputs/scripts'/f))
        for name in ['neworder','payment','beets']:
            check(name+' complete original decision unchanged',select_existing_binding(load(COMMON/(name+'-description.json')),ROOT)==load(COMMON/(name+'-decision.json')))
        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n')
        os.environ['BEETSDIR']=str(out/'config');sys.path.insert(0,str(SOURCE))
        import beets
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Item,Library
        beets.config['plugins']=[]
        check('no installed plugins',not list(plugins.find_plugins()))
        plan=recover_empty_mapping_prefix(Item,'update');state_plan=recover_private_state_step(Item,'clear_dirty')
        save('prefix-plan.json',plan);save('state-plan.json',state_plan)
        prefix=compile_plan(plan,'run_initialization_prefix','generated-prefix.py')
        clear=compile_plan(state_plan,'run_private_state_step','generated-state.py')
        check('virtual update resolved through actual MRO',plan['dispatch']['resolved_owner']=='Item' and plan['dispatch']['base_owner']=='Model')
        check('field demand recovered from source',plan['field_reads']==['mtime'])
        check('neither plan grants lowering',not plan['authorizes_lowering'] and not state_plan['authorizes_lowering'])
        check('compiler contains no application or field-name special case',all(token not in (ROOT/'scripts/initialization_effects.py').read_text() for token in ['beets','mtime','tracktotal']))
        init_field=plan['field_reads'][0];target='tracktotal'
        check('native fields in fixed deployment',all(k in Item._fields and k not in Item._getters() for k in [init_field,target]))

        generic=[]
        for cls in [fixtures.Renamed,fixtures.Inherited]:
            renamed=recover_empty_mapping_prefix(cls,'populate')
            generated=compile_plan(renamed,'run_initialization_prefix','generated-'+cls.__name__+'.py')
            check(cls.__name__+' renaming/inheritance recovered',renamed['field_reads']==['checkpoint'] and renamed['dispatch']['resolved_owner']=='Renamed')
            for mode in ['true','false','eq-error','truth-error']:
                arms=[]
                for arm in ['original','candidate']:
                    events=[];value=fixtures.Comparable(events,mode)
                    obj=cls();obj.checkpoint=value
                    def run():
                        if arm=='original':obj.populate({})
                        else:generated(lambda field:getattr(obj,field))
                        return ()
                    arms.append(dict(outcome=outcome(run),events=events))
                check(cls.__name__+'/'+mode+' comparison and truth effects',arms[0]==arms[1])
                generic.append(dict(case=cls.__name__+'/'+mode,arms=arms))
        renamed_state=recover_private_state_step(fixtures.RenamedState,'reset')
        renamed_clear=compile_plan(renamed_state,'run_private_state_step','generated-renamed-state.py')
        for mode in ['normal','false','truth-error','revision-error']:
            arms=[]
            for arm in ['original','candidate']:
                events=[];resource=fixtures.Resource(events,mode)
                obj=fixtures.RenamedState();obj._owner=resource
                local={'_owner':resource}
                def run_state():
                    if arm=='original':obj.reset()
                    else:renamed_clear(local)
                    return ()
                result=outcome(run_state)
                actual=obj.__dict__ if arm=='original' else local
                arms.append(dict(outcome=result,events=events,touched=len(actual['_touched']),epoch=actual.get('_epoch')))
            check('renamed private state/'+mode+' retained',arms[0]==arms[1])
            generic.append(dict(case='renamed private state/'+mode,arms=arms))
        rejects=[]
        for cls,method,compiler in [(fixtures.ExtraCall,'populate',recover_empty_mapping_prefix),
                (fixtures.ReachableBody,'populate',recover_empty_mapping_prefix),
                (fixtures.ExtraBaseChild,'populate',recover_empty_mapping_prefix),
                (fixtures.PublicState,'reset',recover_private_state_step),
                (fixtures.ResourceWrite,'reset',recover_private_state_step),
                (fixtures.EscapingState,'reset',recover_private_state_step),
                (fixtures.ExternalState,'reset',recover_private_state_step),
                (fixtures.LocalRebinding,'reset',recover_private_state_step)]:
            try:compiler(cls,method)
            except UnsupportedPrefix as exc:rejects.append(dict(case=cls.__name__,reason=str(exc)))
            else:raise AssertionError('unsafe structure accepted: '+cls.__name__)
        for name,cls,method,compiler in [('super',fixtures.Renamed,'populate',recover_empty_mapping_prefix),
                                      ('set',fixtures.RenamedState,'reset',recover_private_state_step)]:
            with patch.object(fixtures,name,lambda:None,create=True):
                try:compiler(cls,method)
                except UnsupportedPrefix as exc:rejects.append(dict(case='shadowed '+name,reason=str(exc)))
                else:raise AssertionError('shadowed builtin accepted: '+name)
        check('ten unsupported source forms rejected',len(rejects)==10)
        save('generic-controls.json',generic);save('unsupported-structures.json',rejects)

        original_hash=fingerprint(BASE/'100/library.db');path=out/'library.db'
        with sqlite3.connect(BASE/'100/library.db') as src,sqlite3.connect(path) as dst:src.backup(dst)
        lib=Library(path,directory=BASE/'100/music');initial=snapshot(path)
        def original(rows,flex,resource,demands):
            item=core.Results(Item,list(rows),resource,list(flex))[0]
            return tuple(getattr(item,k) for k in demands)
        def candidate(rows,flex,resource,demands):
            return consume_packet(core,Item,rows,flex,resource,prefix,clear,demands)[0]
        numeric=[]
        for name,value in VALUES:
            if name!='empty':
                with sqlite3.connect(path) as sql:sql.execute('UPDATE items SET tracktotal=? WHERE album_id=1',(value,))
            before=snapshot(path);packet=lib.items(query.MatchQuery('album_id',999 if name=='empty' else 1))
            rows,flex=list(packet.rows),list(packet.flex_rows)
            a=outcome(lambda:original(rows,flex,lib,[target]));b=outcome(lambda:candidate(rows,flex,lib,[target]))
            check(name+' complete source-prefix decode',a==b)
            check(name+' full database unchanged by decode',snapshot(path)==before)
            check(name+' full row packet',len(rows)==(0 if name=='empty' else 9) and all(len(r.keys())==len(Item._fields) for r in rows))
            numeric.append(dict(case=name,input=value_repr(value),original=a,candidate=b,rows=len(rows),flex_rows=len(flex)))
        save('numeric-cases.json',numeric)
        # Restore only declared test cells, then verify the entire original database.
        with sqlite3.connect(BASE/'100/library.db') as src,sqlite3.connect(path) as dst:
            dst.executemany('UPDATE items SET tracktotal=? WHERE id=?',src.execute('SELECT tracktotal,id FROM items WHERE album_id=1').fetchall())
        check('all original data restored exactly',snapshot(path)==initial)
        packet=lib.items(query.MatchQuery('album_id',1));rows,flex=list(packet.rows),list(packet.flex_rows)
        convert=Item._convert;getters=Item._getters
        effects=[]
        for case in EFFECT_CASES:
            arms=[]
            for arm in ['original','candidate','no_initialization','load_without_predicate','no_private_state']:
                events=[];state=[]
                resource=fixtures.Resource(events,case.removeprefix('resource-') if case.startswith('resource-') else 'normal')
                demands=([init_field,init_field,target] if case=='cache-init' else [target,target] if case=='cache-target' else [target])
                def conversion(cls,key,value):
                    events.append(['convert',key])
                    if (case=='init-error' and key==init_field) or (case=='target-error' and key==target):
                        raise RuntimeError(key+' conversion failure')
                    if key==init_field and case in ['eq-true','eq-false','eq-error','truth-error']:
                        return fixtures.Comparable(events,case.removeprefix('eq-') if case in ['eq-true','eq-false'] else case)
                    return convert(key,value)
                def registry(cls):
                    events.append('getter_registry')
                    return getters()
                def clear_observer(item):
                    core.Model.clear_dirty(item)
                    # Compare owned state, not class-default attribute lookup:
                    # false resources leave _revision unassigned in both arms.
                    state.append(dict(dirty=len(item._dirty),revision=item.__dict__.get('_revision')))
                def clear_candidate(local):
                    if arm!='no_private_state': clear(local)
                    state.append(dict(dirty=len(local['_dirty']),revision=local.get('_revision')))
                def run_candidate():
                    selected_prefix = (lambda load: None) if arm=='no_initialization' else (lambda load: load(init_field)) if arm=='load_without_predicate' else prefix
                    return consume_packet(core,Item,rows,flex,resource,selected_prefix,clear_candidate,demands)[0]
                with ExitStack() as stack:
                    stack.enter_context(patch.object(Item,'_convert',classmethod(conversion)))
                    if case=='getter-registry-effect':stack.enter_context(patch.object(Item,'_getters',classmethod(registry)))
                    if arm=='original':stack.enter_context(patch.object(Item,'clear_dirty',clear_observer))
                    result=outcome(lambda:original(rows,flex,resource,demands)) if arm=='original' else outcome(run_candidate)
                arms.append(dict(arm=arm,outcome=result,events=events,state=state))
            effects.append(dict(case=case,arms=arms))
            save('effect-cases.json',effects)
            check(case+' full effect/order/result/state equivalence', all(arms[0][k]==arms[1][k] for k in ['outcome','events','state']))
        save('effect-cases.json',effects)
        by_case={c['case']:c['arms'][0] for c in effects}
        check('normal source order independently checked',by_case['normal']['events']==[['convert',init_field],'resource_truth','resource_revision',['convert',target]])
        check('initialization failure precedes resource and target',by_case['init-error']['events']==[['convert',init_field]])
        check('cache shares prefix conversion with later demand',by_case['cache-init']['events'].count(['convert',init_field])==1)
        check('repeated consumer demand converts once',by_case['cache-target']['events'].count(['convert',target])==1)
        check('comparison result truth tested once',by_case['eq-true']['events'].count('predicate_truth')==1)
        check('false resource skips revision but retains target',by_case['resource-false']['events']==[['convert',init_field],'resource_truth',['convert',target]])

        matrix=[]
        for c in effects:
            reference=c['arms'][0]
            for observed in c['arms'][1:]:
                matrix.append(dict(case=c['case'],arm=observed['arm'],
                    outcome_equal=reference['outcome']==observed['outcome'],
                    trace_equal=reference['events']==observed['events'],
                    owned_state_equal=reference['state']==observed['state']))
        summary={arm:dict(cases=13,
            outcome_mismatches=sum(not r['outcome_equal'] for r in matrix if r['arm']==arm),
            trace_mismatches=sum(not r['trace_equal'] for r in matrix if r['arm']==arm),
            state_mismatches=sum(not r['owned_state_equal'] for r in matrix if r['arm']==arm),
            any_mismatches=sum(not all(r[k] for k in ['outcome_equal','trace_equal','owned_state_equal']) for r in matrix if r['arm']==arm))
            for arm in ['candidate','no_initialization','load_without_predicate','no_private_state']}
        check('each omitted mechanism has a retained witness', all(summary[a]['any_mismatches']>0 for a in summary if a!='candidate'))
        save('ablation-matrix.json',dict(cases=matrix,summary=summary,
            interpretation='Adversarial mechanism witnesses, not population error rates. Private state and event traces are stronger internal checks than export-output equality.',
            timings=False))
        # Receiver-dependent getters remain outside the experiment. No retry after effects.
        unknown=[]
        for key in [init_field,target]:
            events=[]
            def getter(item):events.append('receiver_getter');return 999
            with patch.object(plugins,'item_field_getters',lambda:{key:getter}):
                a=outcome(lambda:original(rows,flex,lib,[target]))
                original_events=list(events);events.clear()
                b=outcome(lambda:candidate(rows,flex,lib,[target]))
            check(key+' computed getter explicitly unsupported without fallback',b.get('error')=='UnsupportedPrefix' and not events and original_events==['receiver_getter'])
            unknown.append(dict(field=key,original=a,candidate=b,original_events=original_events,candidate_events=events))
        save('getter-boundaries.json',unknown)

        # Actual original query lifecycle; both full SQL reads remain in both arms.
        lifecycle=[];connection=lib._connection()
        for outer in [False,True]:
            arms=[]
            for arm in ['original','candidate']:
                events=[]
                def observed(cls,key,value):
                    with lib._tx_stack() as stack:depth=len(stack)
                    events.append(dict(field=key,depth=depth,sqlite_transaction=connection.in_transaction))
                    return convert(key,value)
                with ExitStack() as stack:
                    if outer:stack.enter_context(lib.transaction())
                    stack.enter_context(patch.object(Item,'_convert',classmethod(observed)))
                    packet=lib.items(query.MatchQuery('album_id',1))
                    if arm=='original':result=outcome(lambda:(packet[0].tracktotal,))
                    else:result=outcome(lambda:candidate(packet.rows,packet.flex_rows,lib,[target]))
                arms.append(dict(outcome=result,events=events))
            check(str(outer)+' actual query lifecycle equal',arms[0]==arms[1])
            check(str(outer)+' both prefix/consumer at original depth',[e['field'] for e in arms[0]['events']]==[init_field,target] and all(e['depth']==int(outer) for e in arms[0]['events']))
            lifecycle.append(dict(outer=outer,arms=arms))
        save('lifecycle-cases.json',lifecycle)

        packet=lib.items();rows,flex=list(packet.rows),list(packet.flex_rows)
        all_original=[(item.id,item.tracktotal,item.mtime) for item in core.Results(Item,list(rows),lib,list(flex))]
        all_candidate=[candidate([row],flex,lib,['id',target,init_field]) for row in rows]
        check('all 100 rows exact typed scalar tuples',len(all_original)==100 and all_original==all_candidate and all(tuple(type(v) for v in a)==tuple(type(v) for v in b) for a,b in zip(all_original,all_candidate)))
        with patch.object(Item,'__init__',side_effect=AssertionError('candidate constructed Item')):
            check('candidate actually eliminates Item construction',candidate(rows,flex,lib,[target])==(all_original[0][1],))
        check('complete five-table database intact',snapshot(path)==initial and len(initial['tables'])==5)
        check('resident connection retained',lib._connection() is connection)
        lib._close()
        check('original baseline bytes unchanged',fingerprint(BASE/'100/library.db')==original_hash)
        save('full-data-check.json',dict(rows=100,original=all_original,candidate=all_candidate,before=initial,after=snapshot(path)))
        save('report.json',dict(status='passed',checks=checks,numeric_cases=len(numeric),effect_cases=len(effects),
            generic_pairs=len(generic),unsupported_structures=len(rejects),computed_getter_boundaries=len(unknown),
            source_prefix_recovered=True,intermediate_item_omitted_in_experiment=True,
            backend_admitted=False,whole_constructor_proved=False,selector_modified=False,performance_measured=False,
            remaining=['Constructor/private-state nonescape and source dispatch assumptions are not a general proof',
                       'Computed getters, receiver identity, tracing, allocation failures and KeyError album fallback stay out of scope',
                       'Both SQL reads retained; earlier read-deletion error counterexamples remain',
                       'No new automatically selected backend or default application replacement']))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
