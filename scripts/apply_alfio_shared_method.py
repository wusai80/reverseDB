#!/usr/bin/env python3
"""Bind frozen semantic scopes to the global index; apply the first project's DP."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from collections import Counter
from alfio_baseline import ROOT, digest
from partial_placement import bind_summary, select_partial_contract
from placement_frontend import select_complete_contract

FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
INDEX=ROOT/'evidence/alfio-structure/20261106-e2dc533105d2'
FORWARD=ROOT/'evidence/alfio-forwarding/20261106-f5e1b6f4d3f7'
GROUP=ROOT/'evidence/alfio-service-groups/20261106-6d78cea0bd77'
RETAIN=ROOT/'evidence/grouped-lookup-admission/20261106-abb2849f09ce'
DEPS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'


def controls(check):
    """Synthetic backend claims test the adapter only, never admit an alf.io backend."""
    def n(name,uses,defines,cost=1):
        return dict(id=name,uses=uses,defines=defines,body_known=True,transaction='tx',
                    java_calls=None if cost is None else dict(base=cost,terms={}),
                    queries=['select value from sample where id = ?'],requires=['same_effect'],
                    database=None if cost is None else dict(lowering='synthetic-test-only',verified_source_contract=True,
                        guarantees=['same_effect'],transaction_policy='same_outer_transaction'))
    nodes=[n('left_read',{'input':'int32'},{'a':'int32'}),n('left_result',{'a':'int32'},{'b':'private_value'}),
           n('unknown',{'b':'private_value'},{'c':'private_value'},None),
           n('right_read',{'c':'private_value'},{'d':'int32'}),n('right_result',{'d':'int32'},{'r':'int32'}),
           n('return',{'r':'int32'},{},0)]
    nodes[-1]['database']=None
    c=dict(nodes=nodes,edges=[dict(before=a['id'],after=b['id'],kind='failure_prefix',resource='original_order') for a,b in zip(nodes,nodes[1:])],
           parameters={},external_inputs={'input':'int32'},wire_types={'int32':dict(host_to_database=True,database_to_host=True)},max_boundary_values=None)
    original=copy.deepcopy(c);r=select_partial_contract(c)
    check('unknown middle phase stays at original position',r['barriers'][0]['index']==2 and [(i['start'],i['stop']) for i in r['islands']]==[(0,2),(3,6)])
    check('unknown costs never summed into a global objective',r['global_cost'] is None and r['whole']['selection'] is None)
    first=r['islands'][0]
    check('escaping private value retained as live-out',first['live_out']=={'b':'private_value'} and first['contract']['nodes'][-1]['analysis_only'])
    check('missing export codec prevents pair fusion',not any(len(s['nodes'])==2 for s in first['result']['selection']['database_regions']))
    check('cross-boundary failure dependencies recorded',any(e['after']=='unknown' for e in first['retained_cross_boundary_edges']))
    check('adapter never mutates input',c==original)
    missing_codec_result=copy.deepcopy(r)
    c['wire_types']['private_value']=dict(host_to_database=True,database_to_host=True)
    r=select_partial_contract(c)
    check('same DP can fuse legal islands separately',[[s['nodes'] for s in i['result']['selection']['database_regions']] for i in r['islands']]==[[['left_read','left_result']],[['right_read','right_result']]])
    check('unknown effect not crossed even with complete codecs',all('unknown' not in s['nodes'] for i in r['islands'] for s in i['result']['selection']['database_regions']))
    c['nodes'][0]['host_retention']=dict(required=True,reason='explicit original boundary')
    rejected=select_partial_contract(c)
    check('retention conflict blocks all partial plans',rejected['status']=='retention_conflict_no_partial_plan' and not rejected['islands'])
    del c['nodes'][0]['host_retention']
    c['nodes'][2]['java_calls']=dict(base=0,terms={'unbounded':1});c['parameters']['unbounded']=None
    check('unbounded cost remains a barrier',select_partial_contract(c)['barriers'][0]['reason']=='unbounded parameter domain')
    renamed=copy.deepcopy(c)
    mapping={n['id']:'unit_'+str(i) for i,n in enumerate(renamed['nodes'])}
    for node in renamed['nodes']:node['id']=mapping[node['id']]
    for e in renamed['edges']:e['before']=mapping[e['before']];e['after']=mapping[e['after']]
    rr=select_partial_contract(renamed)
    check('renaming preserves cuts and objective',[(i['start'],i['stop'],i['result']['selection']['objective']) for i in rr['islands']]==[(i['start'],i['stop'],i['result']['selection']['objective']) for i in r['islands']])
    invalid=copy.deepcopy(c);invalid['edges'][0]['before']='right_result'
    try:select_partial_contract(invalid)
    except ValueError:check('call-graph order cannot overwrite execution order',True)
    else:raise AssertionError('invalid source order accepted')
    return dict(synthetic_original=original,missing_codec_result=missing_codec_result,legal_islands=r,renamed_result=rr,
                authority='synthetic negative/positive controls only; no implementation or runtime admission')


def main():
    out=ROOT/'evidence/alfio-shared-method'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},retained_assets={},database_runs=0,timing_samples=0,deleted_modules=0)
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def retain(base,name):
        p=base/name;assert digest(p)==json.loads((base/'manifest.json').read_text())[name],p
        report['retained_assets'][str(p)]=digest(p);return p
    try:
        for name in ['apply_alfio_shared_method.py','partial_placement.py','placement_frontend.py',
                     'fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py','alfio_baseline.py']:
            p=ROOT/'scripts'/name;dst=out/'inputs/scripts'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst);report['inputs'][str(p.relative_to(ROOT))]=digest(p)
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged first-project '+name,digest(ROOT/'scripts'/name)==digest(retain(FIRST,'inputs/scripts/'+name)))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text());r=select_partial_contract(c)
            check(name+' exact complete selection retained',r['whole']['selection']==json.loads(retain(FIRST,name+'-selection.json').read_text()) and not r['islands'])
            save(name+'-regression.json',r)
        save('adapter-controls.json',controls(check))
        classes={}
        with gzip.open(retain(INDEX,'structure-index.jsonl.gz'),'rt') as f:
            for line in f:
                c=json.loads(line)
                if c['application']:classes[c['class']]=c
        graph=json.loads(retain(INDEX,'business-graph.json').read_text());envelope=set(graph['methods_in_structural_envelope'])
        oldclasses={c['class']:c for c in json.loads(retain(DEPS,'bytecode-facts.json').read_text())['classes']}
        contracts={p.name.removesuffix('-contract.json'):json.loads(retain(FORWARD,p.name).read_text()) for p in sorted(FORWARD.glob('*-contract.json'))}
        contracts['reservation_entry']=json.loads(retain(GROUP,'reservation-entry-contract.json').read_text())
        contracts['service_group_collection']=json.loads(retain(RETAIN,'contract.json').read_text())
        hierarchy=json.loads(retain(GROUP,'hierarchy.json').read_text());save('hierarchy.json',hierarchy)
        methods={m['id']:m for c in classes.values() for m in c['methods']};bound=[];results={};island_summary=[]
        for name,c in contracts.items():
            method=c.get('scope',{}).get('method') or c['source']['method'];owner=method.split('#',1)[0]
            sha=c.get('source_class_sha256') or c.get('source',{}).get('class_sha256') or oldclasses[owner]['sha256']
            hashes={owner:sha}
            if c.get('source_evidence',{}).get('caller_class_sha256'):
                caller=c['source_evidence']['attendee_supplier'].split('#',1)[0]
                hashes[caller]=c['source_evidence']['caller_class_sha256']
            binding=dict(method=method,class_sha256=hashes,authority='prior frozen source-bound summary and original class facts')
            b=bind_summary(c,binding,classes);b['scope_id']=name;b['in_structural_envelope']=method in envelope;bound.append(b)
            check(name+' summary anchor present in global envelope',b['in_structural_envelope'])
            for node in c['nodes']:
                source=node.get('source',{})
                if 'instruction_start' not in source:continue
                actual=[]
                for call in methods[source['method']]['calls']:
                    stop=source['instruction_stop_exclusive']
                    if source['instruction_start']<=call['index'] and (stop is None or call['index']<stop):
                        if call['kind']=='invoke':actual.append((call['index'],'direct_call',call['target']))
                        else:actual.extend((call['index'],'bootstrap_handle',h['target']) for h in call['handles'])
                expected=[(call['index'],call['kind'],call['target']) for call in source['calls']]
                check(name+'/'+node['id']+' original ordered call sites match global bytes',actual==expected)
            before=copy.deepcopy(c);r=select_partial_contract(c);results[name]=r
            check(name+' whole-scope result unchanged',r['whole']==select_complete_contract(c))
            check(name+' original contract untouched',before==c)
            save(name+'-contract.json',c);save(name+'-application.json',r)
            for island in r['islands']:
                selection=island['result']['selection']
                island_summary.append(dict(scope=name,range=[island['start'],island['stop']],nodes=island['original_nodes'],
                    original_calls=selection['original_calls'],selected_calls=selection['selected_calls'],
                    regions=selection['database_regions'],live_in=island['live_in'],live_out=island['live_out'],
                    rejection_kinds=dict(Counter(reason['kind'] for interval in selection['rejected_intervals']
                        if set(interval['nodes']).issubset(island['original_nodes']) for reason in interval['reasons'])),
                    diagnostic_policy='counts exclude intervals containing the analysis-only suffix marker'))
        save('bindings.json',bound);save('local-islands.json',island_summary)
        changed=copy.deepcopy(classes);owner=bound[0]['method'].split('#')[0];changed[owner]['sha256']='changed'
        try:bind_summary(contracts[bound[0]['scope_id']],bound[0]['binding'],changed)
        except ValueError:check('stale binary invalidates semantic binding',True)
        else:raise AssertionError('changed bytes accepted')
        mapped={b['method'] for b in bound}
        coverage=dict(envelope_methods=len(envelope),methods_with_conditioned_summaries=len(mapped),scopes=len(bound),
                      unmapped_methods=sorted(envelope-mapped),global_plan=None,
                      policy='unmapped means no attached semantic summary, not unreachable, pure, zero-cost or deletable; root/child scopes overlap and are never summed',
                      graph_unknown_boundaries_preserved=len(graph['boundaries']))
        save('coverage.json',coverage)
        check('every indexed envelope method accounted for conservatively',mapped.issubset(envelope) and len(mapped)+len(coverage['unmapped_methods'])==len(envelope))
        check('parent calculator still unknown',next(n for n in contracts['reservation_entry']['nodes'] if n['id']=='reservation_total')['java_calls'] is None)
        check('materialization boundaries remain host-retained',
              {n['id'] for n in contracts['service_group_collection']['nodes'] if n.get('host_retention',{}).get('required')}=={'read_items','lookup_definitions'})
        check('all real local plans retain original execution',all(not i['regions'] and i['selected_calls']==i['original_calls'] for i in island_summary))
        check('all real complete plans retain original execution',all(not r['whole']['selection']['database_regions'] for r in results.values() if r['whole']['selection']))
        report.update(status='passed',scopes=len(contracts),phase_nodes=sum(len(c['nodes']) for c in contracts.values()),
                      whole_scope_statuses=dict(Counter(r['whole']['status'] for r in results.values())),
                      new_partial_islands=len(island_summary),partial_phase_nodes=sum(len(i['nodes']) for i in island_summary),
                      local_unknown_barriers=sum(len(r['barriers']) for r in results.values()),
                      actual_database_regions=0,global_plan=None)
        save('conclusion.json',dict(**{k:report[k] for k in ['scopes','phase_nodes','whole_scope_statuses','new_partial_islands','partial_phase_nodes','local_unknown_barriers','actual_database_regions','global_plan']},
             method='same source/effect/ABI obligations and ordered-placement-dp/v1; new generic adapter binds indexed bytes and retains unknown barriers',
             next_evidence='a source-bound shared backend and complete ABI for a multi-call island; current opaque frames and missing/unadmitted lowerings still prohibit changes',
             nonclaims=['no global latency optimum','no removal from graph reachability or observed inactivity','no new runtime or speedup','no neworder/payment backend transplant without an equivalent source contract']))
        for p,sha in report['retained_assets'].items():assert digest(Path(p))==sha
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),islands=report.get('new_partial_islands'))),flush=True)


if __name__=='__main__':main()
