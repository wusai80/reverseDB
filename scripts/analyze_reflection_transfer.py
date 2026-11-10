#!/usr/bin/env python3
"""Validate the existing hash-bound reflection bridge's positional mapping."""
import json
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

from app_db_smoke import ROOT,JDK,LIB,digest
sys.path.insert(0,str(ROOT/'prototype/module_pruning'))
from interprocedural_conditions import analyze


def main():
    baseline=ROOT/'evidence/module-interprocedural/20261105-6a3d9ef1f01a'
    old=json.loads((baseline/'report.json').read_text()); manifest=json.loads((baseline/'manifest.json').read_text())
    previous=json.loads((baseline/'extracted.json').read_text())
    sources=sorted({Path(m['location']['file']) for m in previous['methods'] if m['symbol'].split('#')[0] in
        {'com.oltpbenchmark.api.TpccApp','com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus'}})
    for p in [baseline/'extracted.json',baseline/'bridge-contract.json',*sources]:assert digest(p)==manifest[str(p.relative_to(baseline))]
    for p,h in old['retained_assets'].items():assert digest(Path(p))==h
    out=ROOT/'evidence/module-reflection-transfer'/('20261105-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report={'status':'running','checks':[],'inputs':{},'commands':[],'retained_assets':old['retained_assets'],
            'application_executions':0,'database_runs':0,'deletions':0,'global_edges_removed':0,'data_pruning':False,'model_refits':0}
    def freeze(p):
        dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dest);return dest
    def run(cmd,log,check=True):
        report['commands'].append(list(map(str,cmd)))
        with (out/log).open('w') as stream:r=subprocess.run(list(map(str,cmd)),stdout=stream,stderr=subprocess.STDOUT,timeout=60)
        if check and r.returncode:raise RuntimeError(str(out/log))
        return r.returncode
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    try:
        real=[freeze(p) for p in sources];fixture=freeze(ROOT/'prototype/module_pruning/ReflectionTransferFixture.java')
        extractor=freeze(ROOT/'prototype/module_pruning/DependencyExtract.java')
        bridge=freeze(baseline/'bridge-contract.json');shutil.copy2(bridge,out/'bridge-contract.json')
        for p in [Path(__file__),ROOT/'scripts/app_db_smoke.py',baseline/'manifest.json',baseline/'report.json',
                  *[ROOT/'prototype/module_pruning'/n for n in ['interprocedural_conditions.py','path_conditions.py','call_dag.py','test_reflection_transfer.py','test_interprocedural_conditions.py']]]:freeze(p)
        classes=out/'classes';classes.mkdir();jsonjar=LIB/'json-20240303.jar'
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',classes,extractor],'compile.log')
        cp=next(c[5] for c in old['commands'] if len(c)>5 and c[3]=='research.DependencyExtract')
        cmd=[JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.DependencyExtract',out/'extracted.json',cp,bridge,*real,fixture]
        run(cmd,'extract.log');data=json.loads((out/'extracted.json').read_text())
        reflected=[c for c in data['calls'] if c['location']['file']==str(fixture) and 'reflection' in c]
        def call(name):return next(c for c in reflected if c['caller'].split('#')[-1].split('(')[0]==name)
        for name in ['blocked','allowed','literal','text','alias','numeric','mutable','deferred']:
            check(name+'_positional_mapping',call(name)['reflection_argument_transfer']['status']=='verified_bridge_target')
        refusals={'ambiguous':'target_not_unique_for_exact_receiver','packed':'packed_varargs','nullPack':'packed_varargs',
                  'wrongType':'argument_type_not_proven','unknownType':'argument_type_not_proven','dynamic':'dynamic_method_name',
                  'inherited':'target_not_unique_for_exact_receiver','receiverChanged':'receiver_points_to_unknown',
                  'targetVarargs':'target_varargs_not_supported','widening':'argument_type_not_proven'}
        for name,reason in refusals.items():
            proof=call(name)['reflection_argument_transfer'];check(name+'_refused',proof['status']=='unknown' and proof['reason']==reason)
        fixtures=analyze(data,[str(fixture)],include_reflection=True)
        def hits(name):return [r for r in fixtures['records'] if '#'+name+'(' in r['caller'] and r['via']=='verified_reflection_bridge' and r['inner_target'].endswith('#hit()')]
        for name in ['blocked','literal','text','alias']:
            check(name+'_context_conflict',len(hits(name))==1 and hits(name)[0]['status']=='excluded_by_necessary_guards')
        check('alternative_caller_retained',len(hits('allowed'))==1 and hits('allowed')[0]['status']=='not_refuted')
        for name in ['mutable','deferred']:
            check(name+'_context_unknown',len(hits(name))==1 and hits(name)[0]['status']=='unknown')
        check('numeric_mapping_without_numeric_reasoning',call('numeric')['reflection_argument_transfer']['bindings'][0]['formal'] is None)
        before=analyze(data,list(map(str,real)));after=analyze(data,list(map(str,real)),include_reflection=True)
        def key(r):return (r['outer_site'],r['inner_site'],r['callee'],r['inner_target'],r['via'],r['inner_via'])
        after_index={key(r):r for r in after['records']}
        check('original_bridge_contexts_preserved',all(key(r) in after_index and r['status']==after_index[key(r)]['status'] for r in before['records']))
        actual=[c for c in data['calls'] if c['location']['file'] in set(map(str,real)) and 'reflection' in c]
        summary=[{'caller':c['caller'],'location':c['location'],'requested_name':c['reflection'].get('requested_name'),
                  'mapping':c['reflection_argument_transfer']} for c in actual]
        display=next(c for c in actual if c['reflection'].get('requested_name')=='getOrderLines')
        bindings=display['reflection_argument_transfer']['bindings']
        check('display_five_arguments_correctly_offset',len(bindings)==5 and [b['bridge_argument_index'] for b in bindings]==[2,3,4,5,6])
        check('display_types_bound', [b['formal_type'] for b in bindings]==['java.sql.Connection','int','int','int','com.oltpbenchmark.benchmarks.tpcc.pojo.Customer'])
        check('no_delete_or_runtime_claim',not after['deletion_authorized'] and after['global_edges_removed']==0 and not after['dynamic_execution_observed'])
        # The semantic bridge contract must fail closed if the source bridge changes.
        adapter=next(p for p in real if p.name=='TpccApp.java');mutated=out/'mutation/TpccApp.java';mutated.parent.mkdir()
        text=adapter.read_text();changed=text.replace('target.getClass().getDeclaredMethods()','target.getClass().getMethods()')
        check('bridge_mutation_created',text!=changed);mutated.write_text(changed)
        bad=[mutated if p==adapter else p for p in cmd];bad[4]=out/'mutation-output.json'
        rc=run(bad,'mutation.log',check=False)
        check('bridge_source_mutation_rejected',rc!=0 and 'Reflection bridge contract source changed' in (out/'mutation.log').read_text() and not (out/'mutation-output.json').exists())
        for name,obj in [('fixture-contexts.json',fixtures),('application-before.json',before),('application-after.json',after),('actual-mappings.json',summary)]:
            (out/name).write_text(json.dumps(obj,indent=2)+'\n')
        for name in ['test_reflection_transfer.py','test_interprocedural_conditions.py']:
            run([sys.executable,'-B','-m','unittest','discover','-s',out/'inputs/prototype/module_pruning','-p',name,'-v'],name+'.log')
        new=[r for r in after['records'] if r['via']=='verified_reflection_bridge']
        report.update(status='passed',source_checks=len(report['checks']),reflection_unit_tests=3,one_hop_unit_tests=4,
            actual_bridge_calls=len(actual),actual_mapping_statuses=dict(Counter(c['reflection_argument_transfer']['status'] for c in actual)),
            actual_source_bodies=sum(c['reflection_argument_transfer'].get('body_available',False) for c in actual),
            original_contexts=len(before['records']),total_contexts=len(after['records']),
            target_entry_contexts=len(new),target_entry_statuses=dict(Counter(r['status'] for r in new)),
            external_target_boundaries=sum(r.get('via')=='verified_reflection_bridge' for r in after['boundaries']),
            limitations=['One preexisting hash-bound declared-name/arity bridge only; not general reflection.',
                        'Mapping is conditional on successful bridge entry; original bridge and failures remain.',
                        'Only Boolean/String conditions are substituted; numeric and heap domains not solved.',
                        'Source-context counts include non-root methods and are not dynamic execution counts.'])
        for p,h in old['retained_assets'].items():assert digest(Path(p))==h
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
        print(json.dumps({'status':report['status'],'archive':str(out),'checks':len(report['checks'])}))


if __name__=='__main__':main()
