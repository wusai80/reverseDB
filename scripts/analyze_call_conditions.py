#!/usr/bin/env python3
"""Bounded Java call-site guard analysis. No business execution or pruning."""
import json
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

from app_db_smoke import ROOT, JDK, LIB, digest

sys.path.insert(0, str(ROOT/'prototype/module_pruning'))
from call_dag import from_extraction, condense
from path_conditions import evaluate, analyze_graph


def main():
    baseline = ROOT/'evidence/module-initialization/20261105-9b9711eca669'
    previous = json.loads((baseline/'report.json').read_text())
    manifest = json.loads((baseline/'manifest.json').read_text())
    facts = json.loads((baseline/'extracted.json').read_text())
    sources = sorted({Path(m['location']['file']) for m in facts['methods'] if m['symbol'].split('#')[0] in
        {'com.oltpbenchmark.api.TpccApp','com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus'}})
    for path in [baseline/'extracted.json', baseline/'bridge-contract.json', *sources]:
        assert digest(path)==manifest[str(path.relative_to(baseline))], path
    for path, expected in previous['retained_assets'].items():
        assert digest(Path(path))==expected, path
    out=ROOT/'evidence/module-call-conditions'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report={'status':'running','checks':[], 'inputs':{}, 'commands':[],
            'retained_assets':previous['retained_assets'], 'application_executions':0,'database_runs':0,
            'data_pruning':False,'capabilities_disabled':False,'deletions':0,'model_refits':0}
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT); dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,dest); report['inputs'][str(path.relative_to(ROOT))]=digest(dest)
        return dest
    def run(command,name):
        report['commands'].append(list(map(str,command)))
        with (out/name).open('w') as log:
            result=subprocess.run(list(map(str,command)),stdout=log,stderr=subprocess.STDOUT,timeout=60)
        if result.returncode: raise RuntimeError(str(out/name))
    def check(name, condition):
        if not condition: raise AssertionError(name)
        report['checks'].append(name)
    try:
        real_sources=[freeze(p) for p in sources]
        fixture=freeze(ROOT/'prototype/module_pruning/PathConditionFixture.java')
        extractor=freeze(ROOT/'prototype/module_pruning/DependencyExtract.java')
        bridge=freeze(baseline/'bridge-contract.json'); shutil.copy2(bridge,out/'bridge-contract.json')
        for path in [Path(__file__),ROOT/'scripts/app_db_smoke.py', baseline/'manifest.json',baseline/'report.json',
                     *[ROOT/'prototype/module_pruning'/name for name in
                       ['path_conditions.py','test_path_conditions.py','call_dag.py','test_call_dag.py']]]:
            freeze(path)
        classes=out/'classes'; classes.mkdir()
        jsonjar=LIB/'json-20240303.jar'
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',classes,extractor],'compile.log')
        # Reuse the pinned classpath, not the old extractor or source paths.
        cp=next(c[5] for c in previous['commands'] if len(c)>5 and c[3]=='research.DependencyExtract')
        run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.DependencyExtract',
             out/'extracted.json',cp,bridge,*real_sources,fixture],'extract.log')
        data=json.loads((out/'extracted.json').read_text())
        calls=[c for c in data['calls'] if c['location']['file']==str(fixture)]
        fixture_results=[]
        for call in calls:
            if call['target_symbol'].endswith('PathConditionFixture#hit()'):
                fixture_results.append({'caller':call['caller'],'location':call['location'],
                    'conditions':call['conditions'],'decision':evaluate(call['conditions'])})
        def selected(name):
            return [r for r in fixture_results if '#'+name+'(' in r['caller']]
        for name in ('opposite','stringConflict','switchConflict','nestedUnknown'):
            check(name+'_refuted',len(selected(name))==1 and selected(name)[0]['decision']['status']=='excluded_by_necessary_guards')
        for name in ('changed','changedLater','field','fallthrough','loop','identity','catchBranch','deferred'):
            check(name+'_unknown',len(selected(name))==1 and selected(name)[0]['decision']['status']=='unknown')
        for name in ('alternative','multi'):
            check(name+'_both_alternatives_kept',len(selected(name))==2 and all(r['decision']['status']=='not_refuted' for r in selected(name)))
        check('renamed_variable_supported',selected('renamed')[0]['decision']['status']=='not_refuted')
        inner_eval=[c for c in calls if '#stringConflict(' in c['caller'] and c['expression']=='mode.equals("west")']
        check('evaluating_guard_not_removed_with_body',len(inner_eval)==1 and evaluate(inner_eval[0]['conditions'])['status']=='not_refuted')
        (out/'fixture-results.json').write_text(json.dumps(fixture_results,indent=2)+'\n')
        graph=from_extraction(data,list(map(str,real_sources)),['com.oltpbenchmark.api.TpccApp#main(java.lang.String[])'])
        full=analyze_graph(graph)
        full_counts=dict(Counter(r['status'] for r in full['decisions']))
        check('full_application_no_edges_excluded',full_counts.get('excluded_by_necessary_guards',0)==0)
        check('fixtures_excluded',not any('PathConditionFixture' in n['id'] for n in graph['nodes']))
        display_call=next(c for c in data['calls'] if '#reference(' in c['caller'] and c.get('reflection',{}).get('requested_name')=='getOrderLines')
        type_atom=next(a for a in display_call['conditions']['atoms'] if a.get('values')==['OrderStatus'])
        type_id=type_atom['variable']['id']
        check('type_decl_bound_to_reference_scope',type_atom['variable']['scope']==display_call['caller'])
        view=analyze_graph(graph,{type_id:['OrderStatus']})
        view_counts=dict(Counter(r['status'] for r in view['decisions']))
        check('diagnostic_view_excludes_other_transaction_edges',view_counts.get('excluded_by_necessary_guards',0)>0)
        check('diagnostic_view_retains_display_site',evaluate(display_call['conditions'],{type_id:['OrderStatus']})['status']=='not_refuted')
        payment=next(c for c in data['calls'] if '#reference(' in c['caller'] and c.get('reflection',{}).get('requested_name')=='updateWarehouse')
        check('diagnostic_payment_conflict',evaluate(payment['conditions'],{type_id:['OrderStatus']})['status']=='excluded_by_necessary_guards')
        check('all_source_nodes_preserved',full['projection']['nodes']==view['projection']['nodes']==graph['nodes'])
        check('unconstrained_display_remains_reachable',display_call['reflection']['targets'][0] in condense(full['projection'])['reachability_witnesses'])
        for name,obj in [('graph.json',graph),('full-capability-analysis.json',full),
                         ('orderstatus-diagnostic-view.json',view),('full-capability-dag.json',condense(full['projection'])),
                         ('diagnostic-dag.json',condense(view['projection']))]:
            (out/name).write_text(json.dumps(obj,indent=2)+'\n')
        run([sys.executable,'-B','-m','unittest','discover','-s',out/'inputs/prototype/module_pruning','-p','test_path_conditions.py','-v'],'condition-tests.log')
        run([sys.executable,'-B','-m','unittest','discover','-s',out/'inputs/prototype/module_pruning','-p','test_call_dag.py','-v'],'dag-tests.log')
        report.update(status='passed',structural_checks=len(report['checks']),condition_unit_tests=5,dag_unit_tests=6,
            full_capability={'constraints':{},'edge_counts':full_counts},
            diagnostic_view={'constraints':{type_id:['OrderStatus']},'edge_counts':view_counts,
                'contract_adopted':False,'purpose':'conditional view only; not a reduced-capability application'},
            source_nodes=len(graph['nodes']),source_edges=len(graph['edges']),
            fixture_call_sites=len(fixture_results),fixture_refuted_sites=sum(r['decision']['status']=='excluded_by_necessary_guards' for r in fixture_results),
            deletion_authorized=False,limitations=['Lexical necessary guards only; not a complete path formula or dynamic trace.',
                'No interprocedural argument substitution, heap predicates, loop solving, post-return conditions or recursive invocation contexts.',
                'Unsatisfiable guards exclude a represented site under a declared context, not every caller of its target.',
                'All input/output data and full application capability remain intact.'])
        for path,expected in previous['retained_assets'].items(): assert digest(Path(path))==expected
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
        print(json.dumps({'status':report['status'],'archive':str(out),'checks':len(report['checks'])}))


if __name__=='__main__':
    main()
