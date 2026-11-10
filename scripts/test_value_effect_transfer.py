#!/usr/bin/env python3
"""Freeze on development modules, then measure reserved-module coverage once."""
import collections
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from app_db_smoke import ROOT, JDK, LIB, digest
from value_effect_ir import recover
from test_value_effect_ir import controls


def summarize(facts, graph):
    setters=[b for b in facts['bindings'] if b['operation'].startswith('set')]
    slots={(b['receiver'],b['arguments'][0]['value']) for b in setters if b['arguments']
           and b['arguments'][0].get('op')=='literal' and type(b['arguments'][0].get('value')) is int}
    linked={(p['receiver'],p['slot']) for p in graph['paths']}
    represented={s['receiver'] for s in graph['statements'] if s['status']=='represented'}
    omitted=slots-linked
    return dict(statement_declarations=len(facts['statements']),represented_declarations=len(represented),
                static_setter_sites=len(setters),static_literal_setter_slots=len(slots),
                linked_literal_slots=len(slots&linked),unrepresented_literal_slots=len(omitted),
                unrepresented_slots=[dict(receiver=r,slot=s) for r,s in sorted(omitted)],
                sql_parameter_roles=len(graph['paths']),
                known_java_roles=sum(p['binding_status']=='extracted' for p in graph['paths']),
                unknown_java_roles=sum(p['binding_status']!='extracted' for p in graph['paths']),
                role_counts=dict(collections.Counter(p['role'] for p in graph['paths'])),
                unknown_reasons=dict(collections.Counter(u['reason'] for u in graph['unknowns'])),
                timestamp_write_destinations=[p['destination'] for p in graph['paths'] if p['role']=='write_value'
                                             and p['java_value'] and p['java_value'].get('op')=='timestamp'],
                candidate_pair_features=None,risk_score=None,transfer_accuracy=None)


def main():
    protocol_path=ROOT/'prototype/module_pruning/value_effect_transfer_protocol.json'
    protocol=json.loads(protocol_path.read_text())
    out=ROOT/'evidence/value-effect-transfer'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],development={},targets={},
                new_model_fits=0,new_business_executions=0,target_adaptations=0)
    def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dest)
    def retain(archive,name):
        path=archive/name;assert digest(path)==json.loads((archive/'manifest.json').read_text())[name]
        report['retained_assets'][str(path)]=digest(path);return path
    def run(cmd,log):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120)
        (out/log).write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Command failed: '+str(out/log))
    def measure(module,facts,group):
        graph=recover(facts);save(module+'-graph.json',graph)
        report[group][module]=summarize(facts,graph)
        print(json.dumps(dict(module=module,group=group,**report[group][module])),flush=True)
    try:
        for path in [Path(__file__).resolve(),protocol_path,*[ROOT/x for x in protocol['implementation']]]:freeze(path)
        save('protocol.json',protocol)
        result=controls();save('development-controls.json',result);report['development_checks']=result['count']
        prior=ROOT/protocol['development_facts']
        for module in protocol['development_modules']:
            source=retain(prior,module+'-facts.json');freeze(source)
            measure(module,json.loads(source.read_text()),'development')
        # From this point no implementation or protocol changes are permitted.
        frozen=dict(report['inputs']);save('before-target-freeze.json',dict(implementation_hashes=frozen,
             development=report['development'],controls=result,targets=protocol['target_modules'],target_results_opened=False))
        archive=ROOT/protocol['target_source_archive'];reference=retain(archive,'bundle/reference.jar')
        retained=json.loads((archive/'retained-assets.json').read_text());libraries=sorted(LIB.glob('*.jar'))
        for path in libraries:
            assert digest(path)==retained[str(path)];report['retained_assets'][str(path)]=digest(path)
        classes=out/'classes';classes.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-cp',LIB/'json-20240303.jar','-d',classes,
             ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'],'compile.log')
        cp=':'.join(map(str,[reference,*libraries]))
        for module in protocol['target_modules']:
            path=retain(archive,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+module+'.java');freeze(path)
            run([JDK/'java','-cp',str(classes)+':'+str(LIB/'json-20240303.jar'),'research.JdbcBoundaryExtract',
                 out/(module+'-facts.json'),cp,path],module+'-extract.log')
            measure(module,json.loads((out/(module+'-facts.json')).read_text()),'targets')
            for path,sha in frozen.items():assert digest(ROOT/path)==sha,('post-freeze change',path)
        for path,sha in report['retained_assets'].items():assert digest(Path(path))==sha
        report.update(status='completed',freeze_verified=True,labels_read=False,
             conclusion='Representation coverage only; unknowns retained. No candidate outcomes, fitted risk model or generalization accuracy.')
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('report.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)


if __name__=='__main__':main()
