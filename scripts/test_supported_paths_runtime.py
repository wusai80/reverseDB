#!/usr/bin/env python3
"""Execute bounded fixture traces and deletion controls against frozen analyses."""
import difflib
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from app_db_smoke import ROOT,JDK,LIB,digest


def main():
    archives={
        'local':ROOT/'evidence/module-call-conditions/20261105-92bf37d367f7',
        'direct':ROOT/'evidence/module-interprocedural/20261105-6a3d9ef1f01a',
        'reflection':ROOT/'evidence/module-reflection-transfer/20261105-f5982c8ee396',
    }
    manifests={k:json.loads((p/'manifest.json').read_text()) for k,p in archives.items()}
    def verified(kind,relative):
        path=archives[kind]/relative
        if digest(path)!=manifests[kind][relative]:raise ValueError('Archive changed: '+str(path))
        return path
    names={'local':'PathConditionFixture.java','direct':'InterproceduralFixture.java','reflection':'ReflectionTransferFixture.java'}
    fixtures={k:verified(k,'inputs/prototype/module_pruning/'+v) for k,v in names.items()}
    facts_path=verified('reflection','extracted.json');facts=json.loads(facts_path.read_text())
    contract_path=verified('reflection','bridge-contract.json');contract=json.loads(contract_path.read_text())
    decl=next(m for m in facts['methods'] if m['symbol']==contract['symbol']);loc=decl['location']
    adapter=verified('reflection',str(Path(loc['file']).relative_to(archives['reflection'])))
    bridge_method=adapter.read_text()[loc['start']:loc['end']].strip()
    assert hashlib.sha256(bridge_method.encode()).hexdigest()==contract['method_sha256']
    local_labels=verified('local','fixture-results.json')
    direct_labels=verified('direct','fixture-contexts.json')
    reflection_labels=verified('reflection','fixture-contexts.json')
    source_facts={kind:verified(kind,'extracted.json') for kind in archives}
    exclusions=[r for r in json.loads(local_labels.read_text()) if r['decision']['status']=='excluded_by_necessary_guards']
    assert len(exclusions)==4
    jsonjar=LIB/'json-20240303.jar'
    old_report=json.loads(verified('reflection','report.json').read_text())
    assert digest(jsonjar)==old_report['retained_assets'][str(jsonjar)]
    out=ROOT/'evidence/module-supported-runtime'/('20261105-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report={'status':'running','checks':[],'inputs':{},'commands':[],'application_runs':0,'database_runs':0,
            'production_deletions':0,'data_pruning':False,'model_refits':0,'performance_runs':0}
    def freeze(path):
        target=out/'inputs'/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        report['inputs'][str(path.relative_to(ROOT))]=digest(target);return target
    def run(cmd,log):
        report['commands'].append(list(map(str,cmd)))
        with (out/log).open('w') as stream:r=subprocess.run(list(map(str,cmd)),stdout=stream,stderr=subprocess.STDOUT,timeout=60)
        if r.returncode:raise RuntimeError(str(out/log))
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def dump(name,data): (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    try:
        for path in [*fixtures.values(),*source_facts.values(),contract_path,adapter,local_labels,direct_labels,reflection_labels,
                     Path(__file__),ROOT/'scripts/app_db_smoke.py',*[p/'manifest.json' for p in archives.values()],
                     archives['reflection']/'report.json',jsonjar]:freeze(path)
        harness=freeze(ROOT/'prototype/module_pruning/SupportedPathRuntimeProbe.java')
        texts={k:p.read_text() for k,p in fixtures.items()}
        instrument='static int hits=0; static final java.util.List<String> hitSites=new java.util.ArrayList<>(); static void hit() { hits++; StackTraceElement s=Thread.currentThread().getStackTrace()[2]; hitSites.add(s.getClassName().replace("$",".")+"#"+s.getMethodName()+":"+s.getLineNumber()); }'
        def counted(text):
            assert text.count('static void hit() {}')==1
            return text.replace('static void hit() {}',instrument)
        pruned=texts['local']
        for r in sorted(exclusions,key=lambda r:r['location']['start'],reverse=True):
            a,b=r['location']['start'],r['location']['end'];assert pruned[a:b]=='hit()'
            pruned=pruned[:a]+pruned[b:]
        def replace_once(text,old,new):
            assert text.count(old)==1,(old,text.count(old));return text.replace(old,new)
        variants={
            'original':dict(texts),
            'supported_local_deletion':{**texts,'local':pruned},
            'wrong_shared_target_deletion':{**texts,
                'direct':replace_once(texts['direct'],'static void sink(boolean parameter) { if (!parameter) hit(); }','static void sink(boolean parameter) { if (!parameter) ; }'),
                'reflection':replace_once(texts['reflection'],'private void sink(boolean value) { if (!value) hit(); }','private void sink(boolean value) { if (!value) ; }')},
            'wrong_unknown_as_dead':{**texts,
                'direct':replace_once(texts['direct'],'static void changedFormal(boolean p) { p=false; if(!p) hit(); }','static void changedFormal(boolean p) { p=false; if(!p) ; }'),
                'reflection':replace_once(texts['reflection'],'private void mutable(boolean value) { value=false; if(!value) hit(); }','private void mutable(boolean value) { value=false; if(!value) ; }')},
        }
        protocol={'case_order':'fixed harness order; Boolean false then true; fixed String array order',
            'arms':list(variants),'edits':'Only four globally contradictory local hit call sites in supported arm; other arms intentionally incorrect.',
            'observations':['hit counters','hit caller source lines','void completion','exception class and message'],
            'bridge_scope':'Exact archived invoke method in an isolated minimal TpccApp shell; not full application.',
            'domains':'All Boolean assignments for chosen entries; fixed String examples including null, empty and unseen Unicode; not exhaustive String/data proof.',
            'stop':'One fixed execution per arm; retain failures, do not expand cases after results.',
            'deletion_authorized':False}
        dump('protocol.json',protocol)
        dump('local-deletion-sites.json',exclusions)
        bridge_source='package com.oltpbenchmark.api;\nimport java.lang.reflect.*;\npublic class TpccApp {\n'+bridge_method+'\n}\n'
        results={}
        for arm,variant in variants.items():
            arm_dir=out/arm;src=arm_dir/'src';classes=arm_dir/'classes';src.mkdir(parents=True);classes.mkdir()
            for kind,name in names.items():(src/name).write_text(counted(variant[kind]))
            (src/'TpccApp.java').write_text(bridge_source)
            shutil.copy2(harness,src/harness.name)
            diff=[]
            for kind in names:
                diff.extend(difflib.unified_diff(texts[kind].splitlines(True),variant[kind].splitlines(True),fromfile='original/'+names[kind],tofile=arm+'/'+names[kind]))
            (arm_dir/'edit.diff').write_text(''.join(diff))
            run([JDK/'javac','--release','23','-g','-proc:none','-cp',jsonjar,'-d',classes,*sorted(src.glob('*.java'))],arm+'/compile.log')
            run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'com.oltpbenchmark.api.SupportedPathRuntimeProbe',arm_dir/'observations.json'],arm+'/run.log')
            rows=json.loads((arm_dir/'observations.json').read_text())['cases']
            results[arm]={r['id']:r for r in rows}
            check(arm+'_unique_case_ids',len(results[arm])==len(rows))
        original=results['original'];safe=results['supported_local_deletion']
        for arm,rows in results.items():check(arm+'_same_case_order',list(rows)==list(original))
        check('baseline_expected_hits_and_errors',all(
            sum(row['observed'][k] for k in ['local_hits','direct_hits','reflection_hits'])==row['expected_hits']
            and row['observed']['error']==row['expected_error'] for row in original.values()))
        check('counter_and_trace_agree',all(row['observed'][prefix+'_hits']==len(row['observed'][prefix+'_sites'])
            for rows in results.values() for row in rows.values() for prefix in ['local','direct','reflection']))
        for kind,path in source_facts.items():
            known_sites={c['caller'].split('(')[0]+':'+str(c['location']['line'])
                         for c in json.loads(path.read_text())['calls']
                         if c['target_symbol'].endswith('#hit()') and Path(c['location']['file']).name==names[kind]}
            check(kind+'_runtime_locations_match_frozen_source',bool(known_sites) and all(
                set(row['observed'][kind+'_sites'])<=known_sites for rows in results.values() for row in rows.values()))
        check('supported_deletion_preserves_all_observations',all(row['observed']==safe[id]['observed'] for id,row in original.items()))
        local_sites={r['caller'].split('(')[0]+':'+str(r['location']['line']) for r in exclusions}
        check('refuted_local_source_sites_never_hit',all(not local_sites.intersection(row['observed']['local_sites']) for row in original.values()))
        predictions={}
        # Preserve call-context identity: only assert traces under the matching caller.
        for kind,path,marker in [('direct',direct_labels,'#hit()'),('reflection',reflection_labels,'#hit()')]:
            records=json.loads(path.read_text())['records']
            for record in records:
                if record['status']!='excluded_by_necessary_guards' or not record['inner_target'].endswith(marker):continue
                method=record['caller'].split('#')[-1].split('(')[0]
                target=record['callee'].split('(')[0]+':'+str(record['inner_location']['line'])
                selected=[row for id,row in original.items() if id==kind+'.'+method or id.startswith(kind+'.'+method+'.')]
                check(kind+'.'+method+'_refuted_context_never_hits',bool(selected) and all(target not in row['observed'][kind+'_sites'] for row in selected))
                predictions[kind+'.'+method]={'cases':len(selected),'excluded_site':target}
        check('alternative_contexts_execute',original['direct.allowed.false']['observed']['direct_hits']==1 and original['reflection.allowed.false']['observed']['reflection_hits']==1)
        for kind,path in [('direct',direct_labels),('reflection',reflection_labels)]:
            record=next(r for r in json.loads(path.read_text())['records'] if '#allowed(' in r['caller'] and r['inner_target'].endswith('#hit()'))
            target=record['callee'].split('(')[0]+':'+str(record['inner_location']['line'])
            check(kind+'_positive_context_trace_binding',target in original[kind+'.allowed.false']['observed'][kind+'_sites'])
        check('unknown_controls_execute',all(row['observed']['direct_hits']+row['observed']['reflection_hits']==1 for id,row in original.items() if id.startswith('unknown.')))
        differences={}
        for arm in ['wrong_shared_target_deletion','wrong_unknown_as_dead']:
            differences[arm]=[{'id':id,'original':row['observed'],'mutant':results[arm][id]['observed']} for id,row in original.items() if row['observed']!=results[arm][id]['observed']]
            check(arm+'_detected',bool(differences[arm]))
        dump('prediction-trace-checks.json',predictions);dump('mutation-counterexamples.json',differences)
        report.update(status='passed',checks_passed=len(report['checks']),cases_per_arm=len(original),
            runtime_arms=len(results),fixture_executions=len(original)*len(results),
            supported_deleted_local_sites=4,baseline_candidate_pairs=len(original),
            baseline_candidate_differences=0,incorrect_mutants_detected=len(differences),
            mutant_differing_cases={k:len(v) for k,v in differences.items()},
            bridge_method_sha256=contract['method_sha256'],dynamic_execution_observed=True,
            deletion_authorized=False,limitations=['Instrumented authored fixtures and isolated exact bridge method, not full TPC-C or database execution.',
                'Dynamic agreement supports frozen analysis only on this fixed matrix; does not prove all String inputs.',
                'Correct deletion arm removes only intrinsically contradictory local sites; contextual target deletion is a negative control.',
                'Instrumentation and added observer effects are shared by all arms; no speed measurements.'])
        for kind,path in fixtures.items():assert digest(path)==manifests[kind]['inputs/prototype/module_pruning/'+names[kind]]
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        dump('report.json',report)
        dump('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps({'status':report['status'],'archive':str(out),'checks':len(report['checks']),
                          'cases_per_arm':report.get('cases_per_arm'),'executions':report.get('fixture_executions')}))


if __name__=='__main__':main()
