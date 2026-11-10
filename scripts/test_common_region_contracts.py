#!/usr/bin/env python3
"""One offline compatibility experiment across two legacy cases and beets."""
import copy
import json
from pathlib import Path
import shutil
from uuid import uuid4

from fusion_placement import choose_placement
from fusion_regions import sql_effect
from placement_frontend import select_complete_contract
from region_contracts import describe, normalize, denormalize, fingerprint, select_existing_binding

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
BEETS = ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'
PERF = ROOT/'evidence/beets-scalar-performance/20261109-ac41596489c5'
ADMISSION = ROOT/'evidence/beets-admission/20261109-3a57e2edf0d1'


def main():
    out=ROOT/'evidence/common-region-contracts'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p): return json.loads(p.read_text())
    def save(name,data): (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    def ref(p): return dict(path=str(p.relative_to(ROOT)),sha256=fingerprint(p))
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append(name)
    def rejected(name,fn):
        try: fn()
        except ValueError as exc:
            checks.append(name)
            return str(exc)
        raise AssertionError(name)
    save('protocol.json',dict(cases=['NewOrder','Payment','beets first scalar branch'],
        operation='Lossless common description and exact old-DP binding replay',
        preserve=['original DP code and objective','all fields including unknown costs','all source units and effects','explicit execution domain'],
        no_database_runs=True,no_new_timing=True,no_new_backend=True,
        negatives=['changed binding','lost node','missing encoding','unknown costs','unresolved evidence','renamed symbols']))
    for f in ['region_contracts.py','test_common_region_contracts.py']:
        shutil.copy2(ROOT/'scripts'/f,out/f)
    descriptions={};decisions={};negative={}
    try:
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged',fingerprint(ROOT/'scripts'/f)==fingerprint(FIRST/'inputs/scripts'/f))
        for name in ['neworder','payment']:
            p=FIRST/(name+'-contract.json');program=load(p)
            last=program['nodes'][-1]
            scope=dict(entry=name,execution_domain='Inherit original frozen source/schema/transaction/error contracts unchanged; normal-path call objective does not waive error obligations',
                deployment='Inherited pinned Java/JDBC/PostgreSQL implementation',
                legal_parameters=program['parameters'],interface_inputs=program['external_inputs'],
                interface_outputs=last.get('uses',{}),
                output_role='Typed values consumed by retained return/transaction suffix; not a new external API reconstruction',
                observables=['complete returned values','business checks and exceptions','ordered database writes and locks','retained host identity/type behavior','outer transaction lifecycle'],
                authority='Inherited manual source-bound catalog, not new automatic recovery or proof')
            d=describe(program,scope=scope,provenance=[ref(p),ref(FIRST/'inputs/scripts/fusion_placement_contracts.py')],
                evidence=[ref(FIRST/(name+'-selection.json'))],unresolved=[],dp_binding=ref(p))
            check(name+' full input roundtrip',denormalize(d['program'])==program)
            decision=select_existing_binding(d,ROOT)
            check(name+' complete old result identical',decision['selection']==load(FIRST/(name+'-selection.json')))
            descriptions[name]=d;decisions[name]=decision

            # These are structural validation controls, not new admitted source
            # bindings. Exercise the SAME old validator after a roundtrip.
            renamed=copy.deepcopy(program)
            ids={n['id']:f'u{i}' for i,n in enumerate(renamed['nodes'])}
            values=set(renamed['external_inputs'])|{v for n in renamed['nodes'] for v in n.get('defines',{})}
            names={v:f'v{i}' for i,v in enumerate(sorted(values))}
            renamed['external_inputs']={names[v]:t for v,t in renamed['external_inputs'].items()}
            for n in renamed['nodes']:
                n['id']=ids[n['id']]
                for field in ['uses','defines']:
                    if field in n:n[field]={names[v]:t for v,t in n[field].items()}
                if n.get('transaction'):n['transaction']='renamed-transaction'
                if n.get('database'):n['database']['lowering']='anonymous-backend'
            for e in renamed['edges']:e['before']=ids[e['before']];e['after']=ids[e['after']]
            result=choose_placement(denormalize(normalize(renamed)))
            signature=lambda x:[(s['placement'],s['start'],s['stop']) for s in x['segments']]
            check(name+' symbol names do not alter cuts',signature(result)==signature(decision['selection']))

            unknown=copy.deepcopy(program);unknown['nodes'][0]['java_calls']=None
            result=select_complete_contract(denormalize(normalize(unknown)))
            check(name+' unknown cost never repaired to zero',result['selection'] is None and result['status']=='incomplete_frontend_no_global_plan')
            missing=copy.deepcopy(program);missing['wire_types']={}
            result=choose_placement(denormalize(normalize(missing)))
            check(name+' missing codecs reject original selected layout',signature(result)!=signature(decision['selection']) and
                  any(any(reason['kind']=='missing_wire_codec' for reason in r['reasons']) for r in result['rejected_intervals']))
            tampered=copy.deepcopy(d);tampered['program']['units'][0]['original_calls']['base']+=1
            negative[name+'-changed-cost']=rejected(name+' changed program cannot inherit bound authority',lambda:select_existing_binding(tampered,ROOT))
            missing_unit=copy.deepcopy(d);missing_unit['program']['units'].pop()
            negative[name+'-lost-unit']=rejected(name+' lost unit rejected',lambda:select_existing_binding(missing_unit,ROOT))
            incomplete=copy.deepcopy(d);incomplete['unresolved']=['missing execution-domain evidence']
            check(name+' unresolved binding gets no plan',select_existing_binding(incomplete,ROOT)['selection'] is None)
            missing_effect=copy.deepcopy(program)
            target=next(n for n in missing_effect['nodes'] if n.get('database'))
            target['database']['guarantees']=[]
            result=choose_placement(denormalize(normalize(missing_effect)))
            check(name+' missing effect obligations retain original unit',any(s['placement']=='java' and target['id'] in s['nodes'] for s in result['segments']))
            transaction=copy.deepcopy(program)
            for i,n in enumerate(transaction['nodes']):
                if n.get('transaction'):n['transaction']=f'independent-scope-{i}'
            result=choose_placement(denormalize(normalize(transaction)))
            check(name+' separate transactions never fused',all(s['stop']-s['start']==1 for s in result['database_regions']))

        cases=load(BEETS/'cases.json')
        ordinary=next(c for c in cases if c['case']=='default')['arms'][0]
        sql=[x['sql'] for x in ordinary['queries']]
        context='beets_host_lock_context_not_a_guaranteed_sqlite_read_snapshot'
        def unit(id,inputs,outputs,queries,reads,requires,calls):
            return dict(id=id,uses=inputs,defines=outputs,queries=queries,transaction=context,
                effects=dict(reads=reads,writes=[],locks=[]),requires=requires,
                body_known=False,database=None,java_calls=dict(base=calls,terms={}),
                evidence_status='Manual source interpretation; dynamic effects not universally closed',
                retention_reason='No admitted lowering; source queries and host behaviors remain original')
        units=[
            unit('fixed_read',dict(album_id='python_int',sort='beets_sort',db='sqlite_library'),
                dict(fixed_rows='ordered_full_sqlite_rows'),[sql[0]],['items'],
                ['original SQL, columns and order','read errors and connection effects'],1),
            unit('flex_read',dict(album_id='python_int',sort='beets_sort',db='sqlite_library'),
                dict(flex_rows='sqlite_flex_rows'),[sql[1]],['items','item_attributes'],
                ['fresh second statement view','read errors and connection effects'],1),
            unit('first_materialization',dict(fixed_rows='ordered_full_sqlite_rows',flex_rows='sqlite_flex_rows',db='sqlite_library'),
                dict(first_item='beets_item_identity'),[],[],
                ['empty result exception','constructor effects and identity escape'],0),
            unit('scalar_consumption',dict(first_item='beets_item_identity'),dict(tracktotal='python_int'),[],[],
                ['getter dispatch','original field conversion and failures'],0),
        ]
        edges=[dict(before=units[i]['id'],after=units[i+1]['id'],kind='source_order',resource='original execution order') for i in range(3)]
        edges += [dict(before='fixed_read',after='first_materialization',kind='value_flow',resource='fixed_rows'),
                  dict(before='flex_read',after='first_materialization',kind='value_flow',resource='flex_rows'),
                  dict(before='first_materialization',after='scalar_consumption',kind='value_flow',resource='first_item')]
        program=dict(nodes=units,edges=edges,external_inputs=dict(album_id='python_int',sort='beets_sort',db='sqlite_library'),
            parameters={},wire_types={},max_boundary_values=None,
            contract_authority='Manual local-source description only; no old-DP binding or backend admission')
        scope=dict(entry='Album._albumtotal first branch',deployment='Pinned direct ExportPlugin environment',
            execution_domain='Single first-branch invocation with pinned default SQL-fast sorting from archived direct-export configuration, no injected effects, original reads successful; multidisc and slow sort retain original path',
            input_domain='All data retained; no fixed IDs, row counts, tracktotal values or bounds inferred from the 5000-item fixture',
            interface_inputs=program['external_inputs'],interface_outputs=dict(tracktotal='python_int'),
            observables=['scalar value/type','empty-result and conversion exceptions','observable effects and escaped identities within declared deployment'],
            excluded_behavior='Read-error equivalence is explicitly unproved and refuted in strict domain; no promised automatic fallback')
        d=describe(program,scope=scope,
            provenance=[ref(ROOT/'work/beets/source-e978874d/beets/dbcore/db.py'),ref(ROOT/'work/beets/source-e978874d/beets/library/models.py'),
                        ref(ROOT/'work/beets/source-e978874d/beets/library/library.py')],
            evidence=[ref(BEETS/'cases.json'),ref(ADMISSION/'obligations.json'),ref(PERF/'report.json')],
            unresolved=['no closed source-bound lowering contract for read/model deletion',
                        'nested SQL effects unsupported by existing shared parser',
                        'no verified boundary encoding/decoding and execution adapter',
                        'deployment/connection effects and execution domain not enforced or generally proved'])
        descriptions['beets']=d;decisions['beets']=select_existing_binding(d,ROOT)
        check('beets full description roundtrip',denormalize(d['program'])==program)
        check('beets has no fabricated global plan or cost',decisions['beets']['selection'] is None and
              decisions['beets']['status']=='unbound_description_no_plan')
        check('second SQL re-reads database, not previous rows','fixed_rows' not in units[1]['uses'] and units[1]['effects']['reads']==['items','item_attributes'])
        check('actual nested SQL remains unknown to original parser',all(not sql_effect(q)['known'] for q in sql))
        check('invocation original count equals evidence, not whole-export extrapolation',sum(n['java_calls']['base'] for n in units)==len(ordinary['queries'])==2)
        check('beets all four units retained in description',len(d['program']['units'])==4 and all(n['backend_implementation'] is None for n in d['program']['units']))
        d2=copy.deepcopy(d);d2['scope']['entry']='anonymous-entry'
        check('renaming unbound project does not grant admission',select_existing_binding(d2,ROOT)==decisions['beets'])
        changed=copy.deepcopy(d);changed['provenance'][0]['sha256']='0'*64
        negative['changed-source']=rejected('changed beets source binding rejected',lambda:select_existing_binding(changed,ROOT))
        missing=copy.deepcopy(d);missing['program']['units'][2]['inputs']['missing_value']='unknown'
        negative['missing-producer']=rejected('unresolved producer rejected even without DP binding',lambda:select_existing_binding(missing,ROOT))
        for name in descriptions:
            save(name+'-description.json',descriptions[name]);save(name+'-decision.json',decisions[name])
        save('negative-controls.json',negative)
        save('report.json',dict(status='passed',checks=checks,
            implementation='One lossless common descriptor and exact archived-contract adapter; unchanged original selector',
            result='NewOrder/Payment reproduce complete plans; beets represented but unbound, with no plan or claimed automatic optimization',
            limits=['manual source/effect/scope facts','no automatic candidate discovery or backend generation','no proof from hash equality','no new database or performance run']))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
