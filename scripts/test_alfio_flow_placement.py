#!/usr/bin/env python3
"""Extend source-bound frontend coverage; retain the first project's optimizer."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch
import uuid
from alfio_flow_contracts import build_flow_contracts
from placement_frontend import select_complete_contract
from fusion_placement import choose_placement, cost_value
from fusion_regions import sql_effect

ROOT=Path(__file__).resolve().parents[1]
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
DEPS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'
SOURCE=ROOT/'evidence/alfio-baseline/20261106-788b831dd41c'
PREVIOUS=ROOT/'evidence/alfio-shared-placement/20261106-86a6bd34dc4c'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out=ROOT/'evidence/alfio-flow-placement'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},retained_assets={},new_database_runs=0,new_timing_samples=0)
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def retain(folder,name):
        p=folder/name;assert digest(p)==json.loads((folder/'manifest.json').read_text())[name],p
        report['retained_assets'][str(p)]=digest(p);return p
    try:
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            old=retain(FIRST,'inputs/scripts/'+name)
            check('unchanged core '+name,digest(ROOT/'scripts'/name)==digest(old))
        files=['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py','placement_frontend.py',
               'alfio_placement_contracts.py','alfio_flow_contracts.py','test_alfio_flow_placement.py']
        for name in files:
            p=ROOT/'scripts'/name;dst=out/'inputs/scripts'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst);report['inputs']['scripts/'+name]=digest(p)
        facts=json.loads(retain(DEPS,'bytecode-facts.json').read_text())
        repository=retain(SOURCE,'decompiled/alfio/repository/TicketRepository.java').read_text()
        manager=retain(SOURCE,'decompiled/alfio/manager/TicketReservationManager.java').read_text()
        save('protocol.json',dict(algorithm='unchanged ordered-placement-dp/v1 and SQL effect parser',
            extension='source-bound hierarchical phase contracts plus a project-independent missing-cost gate; no alternative selection algorithm',
            unknown='java_calls=null; no invented zero, measured mean or finite upper bound; no global objective when any cost is unresolved',
            branches='whole conditional/loop phase remains atomic; ordinary initialization is an explicit branch-specific child',
            values='coarse opaque host-frame dependence for newly grouped stages; no assumed wire encoding',
            oracle='same complete inputs must return the exact original DP selection for NewOrder and Payment',
            stop='one static frontend integration; no backend generation, business executions or timing'))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text());old=json.loads(retain(FIRST,name+'-selection.json').read_text())
            result=select_complete_contract(c);save(name+'-gate-result.json',result)
            check(name+' complete input goes unchanged to original selector',result['status']=='selected_with_original_dp' and result['selection']==old)
            missing=copy.deepcopy(c);missing['nodes'][0]['java_calls']=None;missing['nodes'][0]['cost_unknown_reason']='synthetic unknown for gate check'
            with patch('placement_frontend.choose_placement',side_effect=AssertionError('optimizer must not receive missing costs')):
                result=select_complete_contract(missing)
            check(name+' unknown cost blocks globally rather than becoming zero',result['selection'] is None and len(result['unresolved_costs'])==1)
        flows,links=build_flow_contracts(facts,repository,manager);save('hierarchy.json',links)
        for changed_repository,changed_manager,label in [(repository+'\n',manager,'repository'),(repository,manager+'\n','manager')]:
            try:build_flow_contracts(facts,changed_repository,changed_manager)
            except ValueError:check('changed '+label+' cannot reuse manual instruction ranges',True)
            else:raise AssertionError('unsupported source accepted')
        results={};ledger=[]
        for name,c in flows.items():
            save(name+'-contract.json',c);result=select_complete_contract(c);results[name]=result;save(name+'-result.json',result)
            missing=[n for n in c['nodes'] if n.get('java_calls') is None]
            if missing:
                check(name+' preserves every unresolved cost without a global plan',result['selection'] is None and {r['node'] for r in result['unresolved_costs']}=={n['id'] for n in missing})
            else:
                check(name+' uses the original DP exactly',result['selection']==choose_placement(c))
            if 'coverage' in c:
                check(name+' accounts for every call in its declared scope exactly once',c['coverage']['all_accounted_for'] and c['coverage']['call_sites']==c['coverage']['expected_call_sites'])
            for node in c['nodes']:
                source=node.get('source',{})
                ledger.append(dict(scope=name,node=node['id'],java_calls=node.get('java_calls'),
                    unknown_cost=node.get('cost_unknown_reason'),call_sites=len(source.get('calls',[])),
                    lowerable=bool(node.get('database') and node['database'].get('verified_source_contract')),
                    wire_status='coarse host frame; no codec' if name not in ['ordinary_batch','empty_batch_boundary'] else 'previous local ABI',
                    sql_effects=[dict(sql=q,analysis=sql_effect(q)) for q in node.get('queries',[])]))
        check('root, category and initialization remain incomplete',all(results[n]['selection'] is None for n in ['reservation_entry','category_body','ordinary_initialization']))
        promotion=flows['reservation_entry']['nodes'][0]
        check('optional promotion uses original boolean affine cost model',flows['reservation_entry']['parameters']=={'promotion_code_present':[0,1]} and
              [cost_value(promotion['java_calls'],{'promotion_code_present':b}) for b in [0,1]]==[0,1])
        check('conditional promotion SQL retained with callback provenance',len(promotion['queries'])==1 and bool(promotion['source']['conditional_callback']['calls']))
        locked=results['locked_selection']['selection']
        check('bounded and unbounded locking alternatives cost two total calls, not three',locked['original_calls']==locked['selected_calls']==dict(base=2,terms={}) and len(flows['locked_selection']['nodes'][2]['queries'])==2)
        check('no database region selected without a trusted lowering and value ABI',all(not r['selection']['database_regions'] for r in results.values() if r['selection'] is not None))
        for name,old_name in [('ordinary_batch','nonempty'),('empty_batch_boundary','empty')]:
            old=json.loads(retain(PREVIOUS,old_name+'-selection.json').read_text())
            check(name+' prior local selection unchanged',results[name]['selection']==old)
        category=[n['id'] for n in flows['category_body']['nodes']]
        check('initialization and its full field effects precede first-ticket read',category.index('initialize_tickets')<category.index('read_first_ticket'))
        fields=next(n for n in flows['ordinary_initialization']['nodes'] if n['id']=='all_additional_fields')
        check('additional-field effects stay unknown instead of inferred empty from fixture',fields['java_calls'] is None and any('#updateOrInsert(' in c['target'] for c in fields['source']['calls']))
        for link in links:
            check('hierarchy link exists: '+link['node'],link['parent'] in flows and link['child'] in flows and link['node'] in {n['id'] for n in flows[link['parent']]['nodes']})
        save('capability-ledger.json',ledger)
        save('conclusion.json',dict(status='broader_frontend_mapped_global_optimization_withheld',scopes=len(flows),
            phase_nodes=sum(len(c['nodes']) for c in flows.values()),
            incomplete_scopes=[n for n,r in results.items() if r['selection'] is None],
            selected_scopes={n:r['selection']['original_calls'] for n,r in results.items() if r['selection'] is not None},
            known_scope_call_coverage={n:c['coverage'] for n,c in flows.items() if 'coverage' in c},
            limits=['phase grouping is manual and bound to exact method instruction locations',
                    'new frames encode conservative host dependence, not recovered exact value/alias flow',
                    'no full-system no-op or optimum result exists',
                    'special-price initialization branches retained outside ordinary child, not deleted',
                    'empty batch is a method boundary diagnostic; service reachability of zero-ticket request is not asserted'],
            next='close missing cost/effect/value summaries using source evidence and shared frontend capabilities; do not add project-specific selection rules'))
        for path,h in report['retained_assets'].items():assert digest(Path(path))==h
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()};save('manifest.json',manifest)
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),files=len(manifest))))


if __name__=='__main__':main()
