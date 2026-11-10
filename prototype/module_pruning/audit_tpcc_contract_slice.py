#!/usr/bin/env python3
"""Offline verification of contract slicing crossed with existing fusion."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))


def main():
    out=Path(sys.argv[1]).resolve()
    load=lambda name:json.loads((out/name).read_text())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    checks=[]
    def check(name,ok):
        assert ok,name
        checks.append(name)
    manifest,run,protocol,summary=[load(x+'.json') for x in ['manifest','run','protocol','summary']]
    check('all archived files intact',all(sha(out/p)==h for p,h in manifest.items()))
    check('frozen experiment sources intact',all(sha(out/'inputs'/p)==h for p,h in run['inputs'].items()))
    check('external frozen evidence intact',all(sha(Path(p))==h for p,h in run['retained_assets'].items()))
    check('complete successful schedule and cleanup',run['status']=='passed' and len(run['cases'])==40
          and not run['cleanup_errors'] and run['measured_accepted_transactions']==416)
    arms=['original','slice_only','fusion_only','fusion_slice']
    positions=Counter();neighbors=Counter()
    for block in range(8):
        order=[c['arm'] for c in run['cases'] if c['phase']=='measurement' and c['round']==block]
        check(f'block {block} declared order',order==protocol['formal_orders'][block])
        positions.update((a,i) for i,a in enumerate(order));neighbors.update(zip(order,order[1:]))
    check('balanced positions and predecessor effects',len(positions)==16 and set(positions.values())=={2}
          and len(neighbors)==12 and set(neighbors.values())=={2})
    golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231/holdout_original'
    expected={name:json.loads((golden/name).read_text()) for name in
              ['responses.json','observations.json','initial-state.json','final-state.json']}
    for case in run['cases']:
        folder=f"{case['phase']}{case['round']}_{case['arm']}"
        check(folder+' complete result/state oracle',all(load(folder+'/'+n)==v for n,v in expected.items()))
        check(folder+' actual completed replay',(out/folder/'complete').read_text()=='passed\n')
        inv=json.loads((out/folder/'invariants.log').read_text())
        check(folder+' five invariants',len(inv)==5 and not any(inv.values()))
        runtime,traces=load(folder+'/runtime.json'),load(folder+'/jdbc-trace.json')
        check(folder+' complete business execution count',len(runtime)==len(traces)==len(case['samples'])==13)
        for observed,trace,sample in zip(runtime,traces,case['samples']):
            executes=[e for e in trace['events'] if e['op'].startswith('execute')]
            ends=[e for e in trace['events'] if e['op'] in ('commit','rollback')]
            check(folder+'/'+sample['id']+' timing/calls from raw trace',observed['id']==trace['id']==sample['id']
                and observed['duration_ns']==sample['duration_ns'] and sample['execute_calls']==len(executes)
                and sample['execute_ns']==sum(e['duration_ns'] for e in executes)
                and len(ends)==1 and ends[0]['duration_ns']==sample['end_ns'])
        check(folder+' predicted independent region contribution',sum(s['execute_calls'] for s in case['samples'])==
              dict(original=267,slice_only=266,fusion_only=89,fusion_slice=88)[case['arm']])
    labels=dict(zip(arms,['original','query_only','model_only','full']))
    for group,result in summary.items():
        for arm in arms:
            totals=[sum(s['duration_ns'] for s in c['samples'] if group=='all' or s['type']==group)/1e6
                    for c in run['cases'] if c['phase']=='measurement' and c['arm']==arm]
            check(group+'/'+arm+' median recomputed',result['arms'][arm]['median_ms']==statistics.median(totals))
    # Check full interval calculations against the frozen implementation after
    # independent raw-trace and group-total verification above.
    from benchmark_tpcc_contract_slice import summarize
    check('current analysis source matches frozen copy',sha(ROOT/'scripts/benchmark_tpcc_contract_slice.py')==
          sha(out/'inputs/scripts/benchmark_tpcc_contract_slice.py'))
    check('all contrasts and exploratory intervals reproducible',summary==summarize(run['cases']))
    settings=load('settings.json')
    check('durability and concurrency mechanisms retained',all(settings[k]=='on' for k in ['fsync','synchronous_commit','full_page_writes','autovacuum']))
    loads=(out/'class-load.log').read_text().splitlines()
    for arm,folder in [('fusion_only','combined-classes'),('fusion_slice','fusion_slice-classes')]:
        for cls in ['FusedNewOrder','SegmentedPayment']:
            check(arm+'/'+cls+' loaded from intended implementation',any(str(out/folder) in line
                and f'com.oltpbenchmark.api.{cls} source:' in line for line in loads))
    for case in run['cases']:
        status=[s for s in case['samples'] if s['type']=='OrderStatus']
        expected_calls=4 if case['arm'] in ['slice_only','fusion_slice'] else 5
        check('OrderStatus actual removal '+case['phase']+str(case['round'])+case['arm'],len(status)==1 and status[0]['execute_calls']==expected_calls)
    demand=load('demand-decision.json')
    check('same demand criterion admits bounded slice',demand['result']['decision']=='conditionally_deletable')
    check('strict or observable presentation retained',all(c['result']['decision']=='retain' for c in demand['negative_controls']))
    check('unknown stays unknown',demand['unknown_control']['decision']=='unknown')
    pilot=ROOT/'evidence/module-pruning/20261104-490b6c6cef18'
    witness=json.loads((pilot/'results.json').read_text())
    check('archived presentation-error witness intact',sha(pilot/'results.json')==json.loads((pilot/'manifest.json').read_text())['results.json'])
    check('no universal error equivalence inferred',witness['fault_counterexample']['original']['status']=='error'
          and witness['fault_counterexample']['pruned']['status']=='ok')
    frozen=json.loads((ROOT/'paper/fossil-hash-slice-freeze.json').read_text())['sha256']
    check('identical demand algorithm to Fossil study',sha(ROOT/'prototype/module_pruning/demand_model.py')==frozen['prototype/module_pruning/demand_model.py'])
    for arm in ['slice_only','fusion_slice']:
        removed=[l for l in (out/'generated'/arm/'slice.diff').read_text().splitlines() if l.startswith('-') and not l.startswith('---')]
        added=[l for l in (out/'generated'/arm/'slice.diff').read_text().splitlines() if l.startswith('+') and not l.startswith('+++')]
        check(arm+' one bound presentation call erased',removed==['-        invoke(p,"getOrderLines",c,1,d,order.o_id,cust);'] and not added)
    target=out.with_name(out.name+'-audit.json')
    target.write_text(json.dumps(dict(status='passed',checks=checks,replays=40,formal_replays=32,
        raw_experiment=str(out.relative_to(ROOT)),audit_source_sha256=sha(Path(__file__)),
        scope='Offline verification, no new benchmark samples'),indent=2)+'\n')
    print(json.dumps(dict(audit=str(target),checks=len(checks))))


if __name__=='__main__':main()
