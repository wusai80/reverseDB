#!/usr/bin/env python3
"""One bounded static experiment. Reuses verified runtime evidence; no timing run."""
import ast
import copy
import hashlib
import json
import shutil
import uuid
from collections import Counter
from pathlib import Path

from fusion_regions import analyze, sql_effect, plan_neworder, schedule, validate_schedule
from generate_neworder_fusion import lower, emit, HELPER, adapter_variant

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value, message):
    if not value: raise AssertionError(message)


def rejects(action):
    try: action()
    except ValueError: return
    raise AssertionError('Expected fail-closed rejection')


def main():
    out = ROOT/'evidence/fusion-regions'/('20261105-'+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status='running', checks=[], baseline=str(BASE), new_database_runs=0, new_timing_samples=0)
    def save(name, value): (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    def check(name, action):
        action(); report['checks'].append(dict(name=name, status='passed'))
    try:
        sources = ['scripts/fusion_regions.py','scripts/generate_neworder_fusion.py',
                   'scripts/fusion_placement.py','scripts/fusion_placement_contracts.py','scripts/payment_fusion_transfer.py',
                   'scripts/test_fusion_regions.py','scripts/test_neworder_fusion.py']
        for name in sources:
            target = out/'inputs'/name; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,target); ast.parse(target.read_text())
        report['current_inputs'] = {name:digest(ROOT/name) for name in sources}
        previous = json.loads((BASE/'manifest.json').read_text())
        for name, sha in previous.items(): require(digest(BASE/name)==sha,'Changed baseline '+name)
        report['verified_baseline_files'] = len(previous)
        prior_run = json.loads((BASE/'run.json').read_text())
        require(prior_run['status']=='passed' and not prior_run['cleanup_errors'],'Baseline not passed')
        ir = json.loads((BASE/'transaction-ir.json').read_text())
        source_name = Path(ir['sql']['stmtGetCustSQL']['source']['file']).relative_to(ROOT)
        adapter_name = next(n for n in prior_run['inputs'] if n.endswith('/TpccApp.java'))
        source = (BASE/'inputs'/source_name).read_text()
        adapter = (BASE/'inputs'/adapter_name).read_text()
        facts = json.loads((BASE/'extracted.json').read_text())
        check('source frontend reproduces archived IR', lambda: require(
            json.loads(json.dumps(lower(source,adapter,facts,ir['source_sha256'],ir['adapter_sha256'])))==ir,'Changed IR'))
        graph = plan_neworder(ir); save('region-plan.json',graph)
        nodes = [n['id'] for n in graph['nodes']]
        events = schedule(nodes)
        check('one complete region with both outer boundaries retained',lambda: require(
            len(graph['regions'])==1 and graph['regions'][0]['nodes']==nodes[1:-1]
            and len(graph['barriers'])==2,'Wrong region'))
        check('all thirteen SQL sites and complete result values retained',lambda: require(
            graph['regions'][0]['sql_sites']==13 and graph['regions'][0]['live_out']==dict(order_json='json',lines_json='json'),
            'Missing SQL or result'))
        check('source phase schedule accepted',lambda: require(validate_schedule(graph,events)['accepted'],'Rejected original'))
        check('duplicate-key may-alias obligation is data independent',lambda: require(any(
            e['before']=='stage_lines' and e['after']=='flush_stock' and e['kind']=='write_after_read' and e['resource']=='stock'
            for e in graph['edges']),'Missing full-read-before-write obligation'))

        # Mutations of the WHOLE-phase schedule, not concrete item/row values.
        interleave = copy.deepcopy(events)
        interleave.remove(dict(node='flush_stock',event='begin'))
        pos = interleave.index(dict(node='stage_lines',event='begin'))+1
        interleave.insert(pos,dict(node='flush_stock',event='begin'))
        verdict = validate_schedule(graph,interleave); save('early-write-rejection.json',verdict)
        check('early write rejected with stock anti-dependency explanation',lambda: require(
            not verdict['accepted'] and any(e['kind']=='write_after_read' and e.get('resource')=='stock' for e in verdict['violations']),
            'Unsafe phase interleaving admitted'))
        check('normal emitter refuses interleaved candidate',lambda: rejects(lambda: emit(ir,'full',interleave)))
        check('normal emitter refuses implicit operation deletion',lambda: rejects(lambda: emit(ir,'full',[
            e for e in events if e['node']!='getWarehouse'])))
        check('normal emitter refuses duplicated writes',lambda: rejects(lambda: emit(ir,'full',events+schedule(['flush_stock']))))
        check('normal emitter refuses invented node',lambda: rejects(lambda: emit(ir,'full',events+schedule(['unseen_callback']))))
        swaps=[]
        for left in range(1,len(nodes)-2):
            order=nodes.copy(); order[left],order[left+1]=order[left+1],order[left]
            result=validate_schedule(graph,schedule(order)); require(not result['accepted'],'Reordered phase admitted')
            swaps.append(dict(pair=nodes[left:left+2],accepted=result['accepted']))
        save('adjacent-reorder-rejections.json',swaps)
        check('all eleven adjacent internal reorderings rejected',lambda: require(len(swaps)==11,'Wrong bounded matrix'))
        bad=copy.deepcopy(ir); bad['source_sha256']='unknown'
        check('unrecognized source contract rejected',lambda: rejects(lambda: emit(bad,'full')))
        bad_order=copy.deepcopy(ir); bad_order['batch_order'].reverse()
        check('unrecognized batch contract rejected',lambda: rejects(lambda: emit(bad_order,'full')))

        # Effects outside the grammar must not silently turn into pure reads.
        unsupported=[
            'SELECT nextval(?) FROM item WHERE i_id=?',
            'SELECT * FROM item JOIN stock ON i_id=s_i_id WHERE i_id=?',
            'SELECT * FROM item WHERE i_id=(SELECT 1)',
            'SELECT * FROM item WHERE i_id=?; COMMIT',
            'SELECT * FROM item WHERE i_id=? OR i_id=?',
            'UPDATE stock SET s_quantity=external_call(?) WHERE s_i_id=?',
            'WITH x AS (DELETE FROM stock RETURNING *) SELECT * FROM x',
            'CALL external_procedure(?)','COMMIT','DELETE FROM stock WHERE s_i_id=?',
            'INSERT INTO item (i_id,i_price) VALUES (?)']
        for q in unsupported: require(not sql_effect(q)['known'],'Unsupported SQL accepted: '+q)
        check('eleven unsupported SQL forms remain unknown',lambda: require(len(unsupported)==11,'Changed matrix'))
        opaque=copy.deepcopy(ir); opaque['sql']['stmtGetItemSQL']['sql']=unsupported[0]
        opaque_graph=plan_neworder(opaque); save('unknown-sql-plan.json',opaque_graph)
        check('unknown SQL divides candidate regions',lambda: require(
            len(opaque_graph['regions'])==2 and any(b['node']=='stage_lines' and 'unsupported_sql_effect' in b['reasons']
                                                   for b in opaque_graph['barriers']),'Unknown effect crossed'))
        check('emitter refuses partial NewOrder backend region',lambda: rejects(lambda: emit(opaque,'full')))

        # Generic algorithm transfer: no NewOrder node names or table names.
        def unit(id,query,tx='t1',**extra):
            return dict(id=id,queries=[query] if query else [],placement='database',body_known=True,
                        transaction=tx,uses={},defines={},**extra)
        simple=[unit('read','SELECT quantity FROM inventory WHERE id=?'),
                unit('write','UPDATE inventory SET quantity=? WHERE id=?')]
        check('unrelated table names retain read-before-write dependency',lambda: require(any(
            e['kind']=='write_after_read' and e['resource']=='inventory' for e in analyze(simple,{})['edges']),
            'Algorithm depends on stock naming'))
        callback=unit('callback',None); callback['body_known']=False
        cut=analyze([simple[0],callback,simple[1]],{}); save('opaque-body-plan.json',cut)
        check('opaque body prevents crossing despite empty SQL list',lambda: require(
            [r['nodes'] for r in cut['regions']]==[['read'],['write']],'Unknown body ignored'))
        boundary=copy.deepcopy(simple); boundary[1]['transaction']='t2'
        check('different transaction scopes never fused',lambda: require(
            len(analyze(boundary,{})['regions'])==2,'Crossed transaction'))
        host=copy.deepcopy(callback); host.update(body_known=True,placement='host')
        check('known host callback still prevents a region spanning it',lambda: require(
            len(analyze([simple[0],host,simple[1]],{})['regions'])==2,'Crossed host effect'))
        typed=copy.deepcopy(simple); typed[0]['defines']={'row':'native_handle'}; typed[1]['uses']={'row':'native_handle'}
        check('unknown cross-boundary representation blocks both uses',lambda: require(
            not analyze(typed,{})['regions'],'Unsafe representation admitted'))
        missing=copy.deepcopy(simple); missing[1]['uses']={'missing':'int32'}
        check('unresolved value producer prevents placement',lambda: require(
            not analyze(missing,{})['nodes'][1]['eligible'],'Missing producer ignored'))
        duplicate=copy.deepcopy(simple); duplicate[1]['id']='read'
        check('ambiguous node identity refused',lambda: rejects(lambda: analyze(duplicate,{})))
        values=copy.deepcopy(simple); values[0]['defines']={'x':'int32'}; values[1]['defines']={'x':'int32'}
        check('ambiguous value definition refused',lambda: rejects(lambda: analyze(values,{})))

        equality=[]
        for mode in ('full','pruned','early_write','full_fault'):
            sql,mapping=emit(ir,mode)
            require(sql==(BASE/'generated'/f'{mode}.sql').read_text(),'SQL changed '+mode)
            require(mapping==json.loads((BASE/'generated'/f'{mode}-source-map.json').read_text()),'Source map changed '+mode)
            equality.append(dict(mode=mode,sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),sql_and_source_map_equal=True))
        save('backend-equality.json',equality)
        check('four SQL definitions and source maps byte/value equivalent to tested backend',lambda: require(len(equality)==4,'Missing mode'))
        check('outer helper and adapter byte identical to tested backend',lambda: require(
            HELPER==(BASE/'generated/FusedNewOrder.java').read_text() and adapter_variant(adapter)==(BASE/'generated/TpccApp.java').read_text(),
            'ABI changed'))
        probe=json.loads((BASE/'probe-results.json').read_text())
        check('prior real early-write witness remains distinct',lambda: require(
            probe['early_write_counterexample']['original']!=probe['early_write_counterexample']['mutant'],
            'Missing existing runtime witness'))
        save('prior-runtime-witness.json',dict(source=str(BASE/'probe-results.json'),
                                             witness=probe['early_write_counterexample'],new_execution=False))
        report.update(status='passed',checks_passed=len(report['checks']),
                      semantic_nodes=len(nodes),region_nodes=len(graph['regions'][0]['nodes']),
                      dependency_edges=len(graph['edges']),edge_kinds=dict(Counter(e['kind'] for e in graph['edges'])),
                      conclusion='One source-bound candidate; static gates reject unsafe phases and unknown effects. Backend unchanged, no new performance evidence.')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
