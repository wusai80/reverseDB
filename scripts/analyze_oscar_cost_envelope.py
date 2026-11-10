#!/usr/bin/env python3
"""Offline, trace-aligned cost ledger; no database, timing or optimizer changes.

An affected interval is an optimistic opportunity budget, not a certified
removable cost. Aggregated CPU graph costs cannot supply a latency bound.
"""
from collections import defaultdict
import ast
import hashlib
import json
from pathlib import Path
import re
import statistics
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'evidence/oscar-module-profile/20261107-a70b2db5616e'
SCREEN=ROOT/'evidence/oscar-cross-layer-screen/20261107-c38bbd11addf'
BASE=ROOT/'evidence/oscar-baseline/20261106-ed9f704d86d6'
SOURCE=ROOT/'work/oscar/source-a5ed3c9c'


def main():
    out=ROOT/'evidence/oscar-cost-envelope'/('20261108-'+uuid4().hex[:12]);out.mkdir(parents=True)
    inputs={};checks=[]
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    def load(p):return json.loads(p.read_text())
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
    def check(n,v):
        assert v,n
        checks.append(n)
    def retained(folder,name):
        p=folder/name
        assert sha(p)==load(folder/'manifest.json')[name],str(p)
        inputs[str(p.relative_to(ROOT))]=sha(p)
        return load(p)
    codes=retained(PROFILE,'profiles/executed-code.json')
    corrected_path=PROFILE.with_name(PROFILE.name+'-analysis.json')
    corrected=load(corrected_path);inputs[str(corrected_path.relative_to(ROOT))]=sha(corrected_path)
    check('corrected analysis bound to original raw manifest',corrected['input_manifest_sha256']==sha(PROFILE/'manifest.json'))
    screen=retained(SCREEN,'screening.json')
    source_hashes=retained(BASE,'source-manifest.json')
    witnesses=[]
    for rel in ['src/oscar/apps/order/utils.py','src/oscar/apps/partner/abstract_models.py',
                'src/oscar/apps/partner/receivers.py','src/oscar/apps/customer/alerts/utils.py',
                'src/oscar/apps/customer/alerts/receivers.py','src/oscar/apps/analytics/receivers.py']:
        p=SOURCE/rel;check('frozen effect source '+rel,sha(p)==source_hashes[rel]);inputs[str(p.relative_to(ROOT))]=sha(p)
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node,ast.FunctionDef) and node.name in ['place_order','create_line_models','create_line_price_models',
                'create_line_attributes','create_additional_line_models','allocate','update_stock_alerts',
                'send_product_alert_email_for_user','_record_user_order','_update_counter']:
                witnesses.append(dict(file=str(p.relative_to(ROOT)),function=node.name,start=node.lineno,end=node.end_lineno))
    def signature(e):
        sql=e.get('sql')
        if sql and sql.startswith(('SAVEPOINT ','RELEASE SAVEPOINT ','ROLLBACK TO SAVEPOINT ')):
            sql=re.sub(r'"s\d+_x\d+"','"<transient-savepoint>"',sql)
        return e['kind'],sql,e.get('params')
    blocks=[];aligned=[]
    for block in [0,1]:
        control=retained(PROFILE,f'profiles/b{block}-original-control/events.json')
        profiled=retained(PROFILE,f'profiles/b{block}-original-profile/events.json')
        result=retained(PROFILE,f'profiles/b{block}-original-control/result.json')
        check(f'block {block} all 191 event signatures aligned',len(control)==len(profiled)==191 and all(signature(a)==signature(b) for a,b in zip(control,profiled)))
        phases=defaultdict(list);slices=defaultdict(list);rows=[]
        for index,(a,b) in enumerate(zip(control,profiled)):
            if a['kind']!='execute':continue
            frames=[f for f in reversed(b['stack']) if f['module'].startswith('oscar.')]
            names=[codes[f['code']]['name'] for f in frames]
            phase=next((n for n in names if n!='OrderCreator.place_order'),'OrderCreator.place_order (direct)')
            owner=names[-1]
            row=dict(block=block,index=index,phase=phase,owner=owner,sql=a['sql'],params=a['params'],
                     wall_ms=a['wall_ns']/1e6,cpu_ms=a['cpu_ns']/1e6,
                     stack=[dict(name=codes[f['code']]['name'],code_sha256=codes[f['code']]['code_sha256'],offset=f['offset']) for f in frames])
            rows.append(row);phases[phase].append(row)
            if phase=='OrderCreator.create_line_models' and a['sql'].startswith('INSERT INTO '):slices['line_insert_chain'].append(row)
            if owner=='AlertsDispatcher.send_product_alert_email_for_user':
                slices['whole_product_alert_chain'].append(row)
                if 'FROM "customer_productalert"' in a['sql']:slices['product_alert_count_and_rows'].append(row)
            if owner=='AbstractStockRecord.allocate':slices['allocation_update_refresh'].append(row)
            receiver=next((f for f in row['stack'] if f['name']=='_record_user_order'),None)
            if receiver and owner in ['AbstractOrder.num_lines','AbstractOrder.num_items'] and receiver['offset']<204:
                slices['first_user_count_and_sum'].append(row)
        wall=sum(o['wall_ns'] for o in result['orders'])/1e6
        driver=sum(r['wall_ms'] for r in rows)
        commit=sum(e['wall_ns'] for e in control if e['kind']=='commit')/1e6
        check(f'block {block} phase partition covers 187 unique executions',len(rows)==187 and len({r['index'] for rs in phases.values() for r in rs})==187)
        check(f'block {block} driver timer reconciles',abs(driver-sum(o['execute_wall_ns'] for o in result['orders'])/1e6)<1e-9)
        def group(rs):return dict(calls=len(rs),control_driver_wall_ms=sum(r['wall_ms'] for r in rs),control_driver_cpu_ms=sum(r['cpu_ms'] for r in rs),event_indexes=[r['index'] for r in rs])
        for name,count in [('line_insert_chain',26),('whole_product_alert_chain',30),('product_alert_count_and_rows',20),('allocation_update_refresh',20),('first_user_count_and_sum',8)]:
            check(f'block {block} exact slice size {name}',len(slices[name])==count)
        blocks.append(dict(block=block,wall_ms=wall,driver_ms=driver,commit_ms=commit,
                           outside_driver_and_commit_ms=wall-driver-commit,
                           phases={k:group(v) for k,v in phases.items()},slices={k:group(v) for k,v in slices.items()}))
        aligned.extend(rows)
    def aggregate(key):
        result=[]
        for name in blocks[0][key]:
            values=[b[key][name] for b in blocks]
            check('same '+key+' call coverage '+name,values[0]['calls']==values[1]['calls'])
            result.append(dict(name=name,calls=values[0]['calls'],
                control_driver_wall_ms=statistics.median(v['control_driver_wall_ms'] for v in values),
                control_driver_wall_range_ms=[min(v['control_driver_wall_ms'] for v in values),max(v['control_driver_wall_ms'] for v in values)],
                qualified_removable_ms=None))
        return sorted(result,key=lambda r:-r['control_driver_wall_ms'])
    phases=aggregate('phases');slices=aggregate('slices')
    ledger={k:statistics.median(b[k] for b in blocks) for k in ['wall_ms','driver_ms','commit_ms','outside_driver_and_commit_ms']}
    check('control total reproduces corrected analysis',ledger['wall_ms']==corrected['arms']['original']['control_wall_ms'])
    histories=[]
    for folder in ['oscar-performance/20261107-f00dfef1e016','oscar-select-template-performance/20261107-91b894fa6d44',
                   'oscar-analytics-performance/20261107-dea1d83efa2e','oscar-foreign-key-performance/20261108-9054be663e21',
                   'oscar-ordered-handoff-performance/20261108-9f48de23db45']:
        s=retained(ROOT/'evidence'/folder,'summary.json')
        histories.append(dict(evidence=folder,median_paired_speedup=s.get('median_paired_speedup'),
            interval=s.get('exploratory_bootstrap_95'),wins=s.get('candidate_faster_pairs'),calls=s.get('execute_per_trial')))
    check('all five measured candidates lack interval exclusion of one',all(h['interval'][0]<=1<=h['interval'][1] for h in histories))
    barriers=[
        dict(region='stock update and callbacks',calls=81,decision='retain whole region',
             required=['stock write and lock order','same-instance refresh','pre/post save signals','low-stock alert mutation','customer notification capability'],
             smaller_opportunity='20-call allocation subregion already tested; remaining alert packet covers 20 calls and could save 10, not delete all 30 alert calls'),
        dict(region='line creation',calls=41,decision='no closed larger fusion candidate',
             required=['26 complete inserts and generated keys','original model creation/save and conversion','price breakdown and option access','ordered extension callbacks'],
             smaller_opportunity='26 inserts are affected work, not removable work; bulk fusion needs an unproved host-effect/identity/sequence contract'),
        dict(region='analytics',calls=40,decision='retain remaining branches',
             required=['product counters and 7 create continuations','user update and fresh miss reads','original failure/retry and model signals'],
             smaller_opportunity='first user pair already measured; product-counter source-ordered prefixes only save 2 calls in this trace'),
        dict(region='query construction across phases',decision='do not add overlapping profile functions or extrapolate CPU to latency',
             required=['field conversion, object identity and legal-input guards'],
             smaller_opportunity='whole 35-read FK construction slice already tested; profile module cost includes required and overlapping work')]
    save('aligned-control-events.json',aligned)
    save('report.json',dict(status='passed',checks=checks,inputs=inputs,ledger=ledger,blocks=blocks,phases=phases,slices=slices,
        source_witnesses=witnesses,previous_screen=screen['candidates'],performance_history=histories,barriers=barriers,
        profile_observation_factors={k:corrected['arms']['original'][k] for k in ['observation_wall_factor','observation_cpu_factor']},
        cost_definition='Sum of observed driver intervals affected by a candidate; not a removable-cost estimate, guaranteed bound or future-latency prediction',
        net_savings_definition='eliminated host construction + eliminated boundary overhead - guards/encoding - added server work - installation; required SQL work remains',
        decision='No new large effect-closed candidate justified by current evidence; stop performance optimization of this Oscar configuration',
        candidate_selected=None,new_database_runs=0,new_timing_samples=0,optimizer_modified=False,
        limits=['Source ownership of control events is inferred by exact ordered stored signatures from profiled sibling runs, not directly observed on controls',
                'Only transient savepoint names normalized; stored parameter representation is not a byte-exact encoding of arbitrary driver objects',
                'Two original control observations, historical baseline rather than a newly profiled optimized residual',
                'Driver intervals include required SQL/server/transport/scheduling; server measurements overlap and cannot be added',
                'Outside-driver time contains required host work and observation overhead, not removable-module time',
                'Slices overlap phases and each other; only the five phases form an additive partition',
                'Aggregated call graph has cycles; no fabricated execution DAG or proof of unobserved capability absence',
                'No defensible numeric end-to-end savings bound available without effect closure and replacement-cost evidence; null is not zero',
                'Stopping is an evidence-based scope decision, not proof that Oscar cannot be optimized']))
    (out/'analysis.py').write_bytes(Path(__file__).read_bytes())
    save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps(dict(status='passed',output=str(out),checks=len(checks),ledger=ledger,phases=phases,slices=slices),ensure_ascii=False))


if __name__=='__main__':main()
