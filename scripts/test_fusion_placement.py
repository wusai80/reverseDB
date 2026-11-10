#!/usr/bin/env python3
"""Fixed two-program placement verification; no new business/timing executions."""
import copy
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from fusion_placement import choose_placement, inspect_interval, validate_plan, cost_value
from fusion_placement_contracts import neworder_contract, payment_contract
from fusion_regions import schedule
from generate_neworder_fusion import emit as emit_neworder
from generate_payment_segments import generate as emit_payment
from payment_fusion_transfer import SOURCE_SHA as PAYMENT_SHA

ROOT=Path(__file__).resolve().parents[1]
N=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'
G=ROOT/'evidence/fusion-regions/20261105-2909d665c433'
P=ROOT/'evidence/payment-segments/20261105-707221611457'
T=ROOT/'evidence/payment-fusion-transfer/20261105-735301a5f9db'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def require(ok,message):
    if not ok: raise AssertionError(message)
def rejects(action):
    try: action()
    except ValueError: return True
    return False


def exhaustive(contract):
    """Independent recursive enumeration; shares only interval admissibility.

    It verifies optimization given the same constraints, not constraint soundness.
    """
    nodes=contract['nodes']; upper={k:v[1] for k,v in contract['parameters'].items()}
    legal={(i,j):inspect_interval(contract,i,j)['allowed'] for i in range(len(nodes)) for j in range(i+1,len(nodes)+1)}
    count=0; best=None; best_count=0
    def visit(i,calls,moved,regions):
        nonlocal count,best,best_count
        if i==len(nodes):
            count+=1; score=(calls,moved,regions)
            if best is None or score<best: best=score; best_count=1
            elif score==best: best_count+=1
            return
        cost=nodes[i]['java_calls']; java=cost.get('base',0)+sum(upper[k]*v for k,v in cost.get('terms',{}).items())
        visit(i+1,calls+java,moved,regions)
        for j in range(i+1,len(nodes)+1):
            if legal[i,j]: visit(j,calls+1,moved+j-i,regions+1)
    visit(0,0,0,0)
    return dict(plans=count,best=list(best),optimal_plans=best_count)


def anonymize(contract):
    c=copy.deepcopy(contract); ids={n['id']:f'u{i:03d}' for i,n in enumerate(c['nodes'])}
    names=set(c['external_inputs'])|{v for n in c['nodes'] for v in [*n.get('uses',{}),*n.get('defines',{})]}
    values={v:f'v{i:03d}' for i,v in enumerate(sorted(names))}
    c['external_inputs']={values[v]:t for v,t in c['external_inputs'].items()}
    for n in c['nodes']:
        n['id']=ids[n['id']]
        for key in ('uses','defines'): n[key]={values[v]:t for v,t in n.get(key,{}).items()}
        if n.get('transaction'): n['transaction']='opaque_tx'
        if n.get('database'): n['database']['lowering']='opaque_backend_unit'
    for e in c['edges']:
        e['before']=ids[e['before']]; e['after']=ids[e['after']]
        if e.get('resource') in values: e['resource']=values[e['resource']]
    c.pop('evidence',None)
    return c


def main():
    out=ROOT/'evidence/fusion-placement'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},new_database_runs=0,new_timing_samples=0)
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok): require(ok,name); report['checks'].append(name)
    try:
        files=['fusion_placement.py','fusion_placement_contracts.py','test_fusion_placement.py','fusion_regions.py',
               'generate_neworder_fusion.py','generate_payment_segments.py','payment_fusion_transfer.py',
               'test_neworder_fusion.py','test_payment_segments.py','test_fusion_regions.py']
        for name in files:
            src=ROOT/'scripts'/name; dst=out/'inputs/scripts'/name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
            report['inputs']['scripts/'+name]=digest(src)
        counts={}
        for archive in (N,G,P,T):
            manifest=json.loads((archive/'manifest.json').read_text())
            for name,sha in manifest.items(): require(digest(archive/name)==sha,'Changed baseline '+str(archive/name))
            counts[str(archive.relative_to(ROOT))]=len(manifest)
        save('protocol.json',dict(programs=['existing NewOrder','existing segmented Payment'],
              objective='worst normal-path execute count; each DB interval cannot add a call at legal parameter minima',
              tie_break=['fewer moved semantic units','fewer database regions'],
              verification='exhaustive legal interval partitions, renaming, capability/ABI/order counterexamples, unchanged SQL/layout and explicit timestamp ABI repair',
              stop='fixed static experiment only; no new data workloads, runtime measurements or tuned weights'))
        ngraph=json.loads((G/'region-plan.json').read_text()); pgraph=json.loads((P/'region-plan.json').read_text())
        contracts=dict(neworder=neworder_contract(ngraph),payment=payment_contract(pgraph,PAYMENT_SHA))
        selections={}
        for name,contract in contracts.items():
            save(name+'-contract.json',contract); selection=choose_placement(contract); selections[name]=selection
            save(name+'-selection.json',selection)
            check(name+' selects the independently archived layout',
                  [s['nodes'] for s in selection['database_regions']]==[r['nodes'] for r in (ngraph if name=='neworder' else pgraph)['regions']])
            oracle=exhaustive(contract); save(name+'-exhaustive.json',oracle)
            check(name+' DP objective agrees with exhaustive enumeration',selection['objective']==oracle['best'])
            renamed=choose_placement(anonymize(contract))
            check(name+' node/value/transaction/backend names do not affect cut positions',
                  [(s['placement'],s['start'],s['stop']) for s in renamed['segments']]==[(s['placement'],s['start'],s['stop']) for s in selection['segments']])
            points=range(5,16) if name=='neworder' else range(2); key=next(iter(contract['parameters']))
            check(name+' chosen plan never increases calls across entire bounded parameter domain',all(
                cost_value(selection['selected_calls'],{key:x})<=cost_value(selection['original_calls'],{key:x}) for x in points))
        nc,pc=contracts['neworder'],contracts['payment']; ns,ps=selections['neworder'],selections['payment']
        check('NewOrder symbolic costs retain all legal line counts',ns['original_calls']==dict(base=11,terms={'line_count':2}) and ns['selected_calls']==dict(base=1,terms={}))
        check('Payment symbolic costs retain both credit branches',ps['original_calls']==dict(base=8,terms={'bad_credit':1}) and ps['selected_calls']==dict(base=3,terms={'bad_credit':1}))
        # Bind predictions to archived measurements, without collecting new ones.
        scenarios=json.loads((N/'inputs/evidence/tpcc-roundtrip/20261103-9954559e225c/run/inputs/app/tpcc/scenarios/holdout.json').read_text())['commands']
        commands={c['id']:c for c in scenarios}; old_checks=json.loads((N/'probe-results.json').read_text())['checks']
        check('NewOrder predicted execute counts match archived real calls',all(
              cost_value(ns['original_calls'] if r['arm']=='original' else ns['selected_calls'],{'line_count':len(commands[r['id']]['lines'])})==r['execute_calls']
              for r in old_checks if r['arm'] in ('original','full')))
        payment_run=json.loads((P/'run.json').read_text()); payment_observed=[]
        for folder,case in payment_run['cases'].items():
            responses={r['id']:r for r in json.loads((P/folder/'responses.json').read_text())}
            for r in case['payment_executes']:
                response=responses[r['id']]
                if response['status']=='committed':
                    cost=ps['original_calls'] if folder.endswith('_original') else ps['selected_calls']
                    payment_observed.append(cost_value(cost,{'bad_credit':int(response['result']['customer']['c_credit']=='BC')})==r['calls'])
        check('Payment predicted execute counts match all 36 archived executions',len(payment_observed)==36 and all(payment_observed))

        names=[n['id'] for n in nc['nodes']]; stage=names.index('stage_lines')
        private=inspect_interval(nc,stage,stage+1); save('private-stage-rejection.json',private)
        check('splitting out staged data requires an unavailable codec',not private['allowed'] and any(r['kind']=='missing_wire_codec' and r.get('value')=='staged_lines' for r in private['reasons']))
        check('keeping complete read and flush phases together needs no staged-data codec',inspect_interval(nc,stage,names.index('flush_stock')+1)['allowed'])
        events=schedule(names); events.remove(dict(node='flush_stock',event='begin'))
        events.insert(events.index(dict(node='stage_lines',event='begin'))+1,dict(node='flush_stock',event='begin'))
        rejected=validate_plan(nc,ns['segments'],events); save('early-write-rejection.json',rejected)
        check('early stock write rejected with read-write obligation',not rejected['accepted'] and any(v['kind']=='write_after_read' and v.get('resource')=='stock' for v in rejected['violations']))
        check('missing operation rejected',not validate_plan(nc,ns['segments'][:-1])['accepted'])
        check('duplicated operation rejected',not validate_plan(nc,ns['segments']+[ns['segments'][-1]])['accepted'])
        opaque=copy.deepcopy(nc); opaque['nodes'][3]['database']=None; changed=choose_placement(opaque)
        save('revoked-lowering-selection.json',changed)
        check('revoking one lowering changes the plan and retains that Java unit',changed['selected_calls']!=ns['selected_calls'] and any(s['placement']=='java' and s['start']==3 for s in changed['segments']))
        unknown=copy.deepcopy(pc); unknown['nodes'][1]['queries']=['SELECT external_call(?) FROM warehouse WHERE w_id=?']
        changed=choose_placement(unknown)
        check('unknown SQL remains Java despite a claimed lowering',any(s['placement']=='java' and s['start']==1 for s in changed['segments']))
        unknown_body=copy.deepcopy(pc); unknown_body['nodes'][1]['body_known']=False
        check('unknown method body stays Java even when its SQL is recognized',any(s['placement']=='java' and s['start']==1 for s in choose_placement(unknown_body)['segments']))
        singleton=copy.deepcopy(pc)
        for n in singleton['nodes']:
            if n['id']!='result': n['database']=None
        check('a standalone one-query lowering is not moved without call savings',not choose_placement(singleton)['database_regions'])
        bad=copy.deepcopy(pc); middle=next(i for i,n in enumerate(bad['nodes']) if n['id']=='original_customer_and_credit_java')
        bad['nodes'][middle]['transaction']='outer_serializable'
        bad['nodes'][middle]['database']=dict(lowering='naive-decimal-and-codepoint',verified_source_contract=True,
            guarantees=['decimal_arithmetic','codepoint_substring'],transaction_policy='same_outer_transaction')
        refused=inspect_interval(bad,middle,middle+1); save('wrong-money-string-rejection.json',refused)
        check('decimal/codepoint substitution cannot discharge Java semantic requirements',not refused['allowed'] and any(r['kind']=='unmet_semantic_obligation' for r in refused['reasons']))
        check('bad monetary capability still selects original Java computation',any(s['placement']=='java' and s['start']==middle for s in choose_placement(bad)['segments']))
        transaction=copy.deepcopy(pc); transaction['nodes'][3]['transaction']='different_transaction'
        check('database region cannot span transaction scopes',not inspect_interval(transaction,1,5)['allowed'])
        commit=copy.deepcopy(pc); commit['nodes'][1]['database']['transaction_policy']='commit_inside'
        check('lowering with an internal commit is rejected',any(r['kind']=='transaction_policy' for r in inspect_interval(commit,1,5)['reasons']))
        for name,c in contracts.items():
            limited=copy.deepcopy(c); limited['max_boundary_values']=0; limited_plan=choose_placement(limited)
            check(name+' zero transfer budget keeps all original operations',not limited_plan['database_regions'] and limited_plan['selected_calls']==selections[name]['original_calls'])
        codec=copy.deepcopy(nc); codec['wire_types']['json']['host_to_database']=False
        check('missing input encoder rejects whole NewOrder region',any(r['kind']=='missing_wire_codec' for r in inspect_interval(codec,1,len(codec['nodes'])-1)['reasons']))
        nullable=copy.deepcopy(nc); nullable['parameters']['line_count']=[0,15]
        check('conditional zero-call original phase cannot become an unconditional DB call',any(r['kind']=='may_add_call_at_legal_minimum' for r in inspect_interval(nullable,stage,stage+1)['reasons']))
        unresolved=copy.deepcopy(nc); unresolved['nodes'][stage]['uses']['not_produced']='int32'
        check('unresolved producer fails closed',rejects(lambda: choose_placement(unresolved)))

        nir=json.loads((N/'transaction-ir.json').read_text()); equality=[]
        for mode in ('full','pruned','early_write','full_fault'):
            sql,mapping=emit_neworder(nir,mode)
            require(sql==(N/'generated'/f'{mode}.sql').read_text(),'Changed NewOrder SQL')
            require(mapping==json.loads((N/'generated'/f'{mode}-source-map.json').read_text()),'Changed source map')
            equality.append(dict(artifact='neworder/'+mode,sha256=hashlib.sha256(sql.encode()).hexdigest()))
        check('NewOrder selected layout emits four unchanged SQL/source-map artifacts',len(equality)==4)
        source=P/'inputs/evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/Payment.java'
        adapter=P/'inputs/evidence/tpcc-observation/20261103-8729d2283fdc/run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        emitted=emit_payment(source.read_text(),adapter.read_text(),json.loads((T/'extracted.json').read_text()))
        for key,file in [('sql','install.sql'),('helper','SegmentedPayment.java'),('adapter','TpccApp.java')]:
            expected=(P/'generated'/file).read_text()
            if key=='helper':
                expected=expected.replace('        s.setString(10,new Timestamp(cmd.getLong("clock_ms")).toString());',
                    '        // Keep the original driver timestamp codec, including infinity sentinels.\n'
                    '        s.setTimestamp(10,new Timestamp(cmd.getLong("clock_ms")));')
            require(emitted[key]==expected,'Unexpected Payment change '+file)
            equality.append(dict(artifact='payment/'+file,sha256=hashlib.sha256(emitted[key].encode()).hexdigest()))
        check('Payment selected layout unchanged except explicit JDBC timestamp ABI repair',len(equality)==7)
        check('Payment source map and legacy graph unchanged',emitted['source_map']==json.loads((P/'generated/source-map.json').read_text()) and emitted['plan']==pgraph)
        check('Payment emitter uses same optimizer selection',emitted['automatic_placement']['segments']==ps['segments'])
        save('backend-equality.json',equality)
        report.update(status='passed',checks_passed=len(report['checks']),verified_baseline_files=counts,
                      selected_calls={k:v['selected_calls'] for k,v in selections.items()},
                      conclusion='Name-independent DP chooses existing layouts from manual semantic/ABI capabilities; exhaustive optimum in a bounded interval model, SQL/layout unchanged, Java timestamp binding repaired; no new speed claim.')
    except BaseException as e:
        report.update(status='failed',error=repr(e)); raise
    finally:
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
