#!/usr/bin/env python3
"""Keep discovered semantic obligations in the shared gate; no new DB run."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from unittest.mock import patch
from grouped_lookup_contracts import grouped_lookup_contract
from placement_frontend import select_complete_contract
from fusion_placement import inspect_interval

ROOT=Path(__file__).resolve().parents[1]
PREVIOUS=ROOT/'evidence/alfio-service-groups/20261106-6d78cea0bd77'
AUDIT=ROOT/'evidence/alfio-service-join-audit/20261106-11a212bff8f2'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
ORDERED=ROOT/'evidence/alfio-ordered-service/20261106-ce2f659e8185'


def main():
    out=ROOT/'evidence/grouped-lookup-admission'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},retained_assets={},database_runs=0,timing_samples=0)
    def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def retain(folder,name):
        p=folder/name
        assert digest(p)==json.loads((folder/'manifest.json').read_text())[name]
        report['retained_assets'][str(p)]=digest(p);return p
    def check(name,condition):
        if not condition:raise AssertionError(name)
        report['checks'].append(name)
    try:
        for name in ['test_grouped_lookup_admission.py','grouped_lookup_contracts.py','placement_frontend.py',
                     'fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            src=ROOT/'scripts'/name;dst=out/'inputs/scripts'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
            report['inputs'][str(src.relative_to(ROOT))]=digest(src)
        audit=json.loads(retain(AUDIT,'admission.json').read_text());retain(AUDIT,'result.json');retain(AUDIT,'runtime.json');retain(AUDIT,'missing-definition.json')
        check('real audit withholds admission',audit['admitted'] is False)
        ordered=json.loads(retain(ORDERED,'result.json').read_text());retain(ORDERED,'admission.json')
        mapping=next(c for c in ordered['cases'] if c['case']=='materialization timing counterexample')
        check('real per-query mapping counterexample retained',mapping['original_lookup_keys']==[101] and mapping['raw_server_attempts']==2 and not mapping['candidate_admitted'])
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged '+name,digest(ROOT/'scripts'/name)==digest(retain(FIRST,'inputs/scripts/'+name)))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text());r=json.loads(retain(FIRST,name+'-selection.json').read_text())
            check(name+' same original selection',select_complete_contract(c)['selection']==r)
        old=json.loads(retain(PREVIOUS,'service-group-contract.json').read_text())
        contract=grouped_lookup_contract('I',old['nodes'][0]['queries'][0],old['nodes'][2]['queries'][0],old['source'])
        save('contract.json',contract)
        result=select_complete_contract(contract);save('selection.json',result)
        check('original grouped selection unchanged',result==json.loads(retain(PREVIOUS,'service-group-result.json').read_text()))
        added={'preserve_statement_snapshots_and_visibility','preserve_lookup_cardinality_failures','preserve_privilege_checks_on_empty_input',
               'preserve_per_query_materialization_timing'}
        check('semantic obligations present in shared representation',all(added.issubset(n['requires']) for n in contract['nodes']))
        check('actual nodes remain host only',all(n['database'] is None for n in contract['nodes']))
        retained=[n for n in contract['nodes'] if n.get('host_retention',{}).get('required')]
        check('original item and definition materializers explicitly retained',
              {n['id'] for n in retained}=={'read_items','lookup_definitions'})
        loop=contract['nodes'][2]['loop_effect_contract']
        check('mapping success is a prerequisite for next lookup',
              loop['order']==['query_current_group','original_host_materialization','emit_current_pair','advance_to_next_group']
              and loop['advance_requires']=='original_host_materialization_completed_successfully'
              and loop['on_mapping_failure']=='propagate_original_exception_without_querying_later_groups')
        conflict_results=[]
        for original_node in retained:
            conflict=copy.deepcopy(contract)
            node=next(n for n in conflict['nodes'] if n['id']==original_node['id'])
            node['database']=dict(verified_source_contract=True,lowering='synthetic_claim_only',
                transaction_policy='same_outer_transaction',guarantees=list(node['requires']))
            before=copy.deepcopy(conflict)
            with patch('placement_frontend.choose_placement',side_effect=AssertionError('retained boundary must not reach optimizer')):
                rejected=select_complete_contract(conflict)
            check(original_node['id']+' cannot be moved merely by asserting guarantees',
                  rejected['status']=='retention_conflict_no_plan' and rejected['selection'] is None
                  and rejected['retention_conflicts'][0]['node']==original_node['id'])
            check(original_node['id']+' conflicting input not silently rewritten',conflict==before)
            conflict_results.append(rejected)
        renamed=copy.deepcopy(conflict)
        for node in renamed['nodes']:node['id']='unrelated_'+node['id']
        for edge in renamed['edges']:
            edge['before']='unrelated_'+edge['before'];edge['after']='unrelated_'+edge['after']
        with patch('placement_frontend.choose_placement',side_effect=AssertionError('renaming must not remove retention')):
            rejected=select_complete_contract(renamed)
        check('retention is independent of project and node names',rejected['status']=='retention_conflict_no_plan')
        save('retention-negative-controls.json',conflict_results)
        # Deliberately insufficient hypothetical backend: this is a negative gate
        # test, not a capability catalog entry or an implementation admission.
        synthetic=copy.deepcopy(contract);n=synthetic['nodes'][2]
        n['database']=dict(verified_source_contract=True,lowering='synthetic_negative_control_only',
                           transaction_policy='same_outer_transaction',guarantees=sorted(set(n['requires'])-added))
        denied=inspect_interval(synthetic,2,3);save('negative-control.json',denied)
        missing={obligation for reason in denied['reasons'] if reason['kind']=='unmet_semantic_obligation' for obligation in reason['obligations']}
        check('existing generic DP gate rejects absent semantic guarantees',not denied['allowed'] and missing==added)
        save('conclusion.json',dict(status='original_host_materialization_retained_no_cross_boundary_fusion',
            new_obligations=sorted(added),gate='original inspect_interval unmet_semantic_obligation check; no project-specific selection rules',
            retention='shared pre-selector rejects database capabilities conflicting with explicit host retention; original selector unchanged',
            retained_nodes=[n['id'] for n in retained],newly_deleted_modules=0,
            decision='retain original query/mapping/next-query sequence; no normal-data restriction, replacement enum checks or batching past mapper',
            counterexample_evidence=[str(AUDIT),str(ORDERED)],global_plan=None,new_database_runs=0,new_timing_samples=0))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))))


if __name__=='__main__':main()
