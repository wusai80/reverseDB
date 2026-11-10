#!/usr/bin/env python3
"""Bounded one-hop source-bound argument transfer; no program mutation."""
import json
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

from app_db_smoke import ROOT, JDK, LIB, digest

sys.path.insert(0,str(ROOT/'prototype/module_pruning'))
from interprocedural_conditions import analyze


def main():
    baseline=ROOT/'evidence/module-call-conditions/20261105-92bf37d367f7'
    old=json.loads((baseline/'report.json').read_text());manifest=json.loads((baseline/'manifest.json').read_text())
    data=json.loads((baseline/'extracted.json').read_text())
    sources=sorted({Path(m['location']['file']) for m in data['methods'] if m['symbol'].split('#')[0] in
        {'com.oltpbenchmark.api.TpccApp','com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus'}})
    for path in [baseline/'extracted.json',baseline/'bridge-contract.json',*sources]:
        assert digest(path)==manifest[str(path.relative_to(baseline))],path
    for path,expected in old['retained_assets'].items(): assert digest(Path(path))==expected,path
    out=ROOT/'evidence/module-interprocedural'/('20261105-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report={'status':'running','checks':[],'inputs':{},'commands':[], 'retained_assets':old['retained_assets'],
        'application_executions':0,'database_runs':0,'deletions':0,'model_refits':0,'capabilities_disabled':False,'data_pruning':False}
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dest);return dest
    def run(command,log):
        report['commands'].append(list(map(str,command)))
        with (out/log).open('w') as stream:
            result=subprocess.run(list(map(str,command)),stdout=stream,stderr=subprocess.STDOUT,timeout=60)
        if result.returncode: raise RuntimeError(str(out/log))
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    try:
        real=[freeze(p) for p in sources]
        fixture=freeze(ROOT/'prototype/module_pruning/InterproceduralFixture.java')
        extractor=freeze(ROOT/'prototype/module_pruning/DependencyExtract.java')
        bridge=freeze(baseline/'bridge-contract.json');shutil.copy2(bridge,out/'bridge-contract.json')
        for path in [Path(__file__),ROOT/'scripts/app_db_smoke.py',baseline/'report.json',baseline/'manifest.json',
                     *[ROOT/'prototype/module_pruning'/name for name in ['interprocedural_conditions.py','test_interprocedural_conditions.py','path_conditions.py','call_dag.py']]]:
            freeze(path)
        classes=out/'classes';classes.mkdir();jsonjar=LIB/'json-20240303.jar'
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',classes,extractor],'compile.log')
        cp=next(c[5] for c in old['commands'] if len(c)>5 and c[3]=='research.DependencyExtract')
        run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.DependencyExtract',out/'extracted.json',cp,bridge,*real,fixture],'extract.log')
        facts=json.loads((out/'extracted.json').read_text())
        fixture_result=analyze(facts,[str(fixture)])
        def hits(caller):
            return [r for r in fixture_result['records'] if '#'+caller+'(' in r['caller'] and r['inner_target'].endswith('#hit()')]
        for name in ['blocked','literalBlocked','sameActual','stringBlocked']:
            check(name+'_context_refuted',len(hits(name))==1 and hits(name)[0]['status']=='excluded_by_necessary_guards')
        for name in ['allowed','literalAllowed','distinctActual']:
            check(name+'_context_retained',len(hits(name))==1 and hits(name)[0]['status']=='not_refuted')
        for name in ['changedActual','changedFormalCaller','varargsCaller','virtualCaller','deferred','recursiveCaller']:
            check(name+'_unknown',len(hits(name))==1 and hits(name)[0]['status']=='unknown')
        overloads=hits('overloadCaller')
        check('overloads_resolved_by_signature',len(overloads)==2 and {r['status'] for r in overloads}=={'excluded_by_necessary_guards','not_refuted'})
        check('same_target_has_alternative_feasible_context',hits('blocked')[0]['callee']==hits('allowed')[0]['callee'])
        check('recursive_method_recognized',any('#recursive(' in m for m in fixture_result['recursive_methods']))
        business=analyze(facts,list(map(str,real)))
        check('fixture_isolation',not any('InterproceduralFixture' in json.dumps(r) for r in business['records']+business['boundaries']))
        check('no_global_edges_removed',business['global_edges_removed']==fixture_result['global_edges_removed']==0)
        check('no_deletion_authorization',business['deletion_authorized'] is fixture_result['deletion_authorized'] is False)
        for name,obj in [('fixture-contexts.json',fixture_result),('application-contexts.json',business)]:
            (out/name).write_text(json.dumps(obj,indent=2)+'\n')
        run([sys.executable,'-B','-m','unittest','discover','-s',out/'inputs/prototype/module_pruning','-p','test_interprocedural_conditions.py','-v'],'tests.log')
        report.update(status='passed',source_checks=len(report['checks']),unit_tests=4,
            fixture_hit_contexts=sum(r['inner_target'].endswith('#hit()') for r in fixture_result['records']),
            fixture_status_counts=dict(Counter(r['status'] for r in fixture_result['records'] if r['inner_target'].endswith('#hit()'))),
            application_contexts=len(business['records']),application_status_counts=dict(Counter(r['status'] for r in business['records'])),
            external_boundaries=len(business['boundaries']),deletion_authorized=False,
            limitations=business['limitations'])
        for path,expected in old['retained_assets'].items():assert digest(Path(path))==expected,path
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
        print(json.dumps({'status':report['status'],'archive':str(out),'checks':len(report['checks'])}))


if __name__=='__main__':main()
