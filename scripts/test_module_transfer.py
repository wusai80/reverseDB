#!/usr/bin/env python3
"""Frozen, unchanged-analyzer transfer coverage; not classifier evaluation."""
import collections
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from app_db_smoke import ROOT, JDK, LIB, digest
from jdbc_boundary_contracts import inspect_binding, unknown_term
from cross_layer_dependencies import recover


def summarize(facts, graph):
    setters = [b for b in facts['bindings'] if b['operation'].startswith('set')]
    requests = sorted({(b['owner'], b['arguments'][0]['value']) for b in setters
                       if b['arguments'] and b['arguments'][0].get('op') == 'literal'
                       and type(b['arguments'][0].get('value')) is int})
    inspected = [dict(owner=owner, slot=slot, result=inspect_binding(facts, owner, slot))
                 for owner, slot in requests]
    reasons = collections.Counter(r['result'].get('reason', 'extracted') for r in inspected)
    timestamps = [b for b in setters if b['operation'] == 'setTimestamp']
    return dict(
        statement_declarations=len(facts['statements']),
        finite_sql_declarations=sum(s['status'] == 'finite_sql_set' for s in facts['statements']),
        setter_sites=len(setters),
        known_value_term_sites=sum(len(b['arguments']) == 2 and not unknown_term(b['arguments'][1]) for b in setters),
        inspect_binding_requests=len(inspected),
        extracted_binding_requests=sum(r['result']['status'] == 'extracted' for r in inspected),
        binding_reasons=dict(reasons), binding_details=inspected,
        timestamp_roots=len(timestamps), recovered_timestamp_paths=len(graph['paths']),
        timestamp_unknown_reasons=dict(collections.Counter(x['reason'] for x in graph['unknowns'])),
        graph_nodes=len(graph['nodes']), graph_edges=len(graph['edges']),
        complete_dependency_graph=False, candidate_pair_features=None, risk_score=None,
        transfer_accuracy=None, deletion_authorized=False)


def main():
    protocol_path=ROOT/'prototype/module_pruning/module_transfer_protocol.json'
    protocol=json.loads(protocol_path.read_text())
    out=ROOT/'evidence/module-transfer'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],modules={},new_model_fits=0,new_business_executions=0)
    def save(name,obj): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def freeze(p):
        dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dest)
    def run(cmd,log):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120)
        (out/log).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Command failed: '+str(out/log))
    try:
        archive=ROOT/protocol['input_archive'];manifest=json.loads((archive/'manifest.json').read_text())
        retained=json.loads((archive/'retained-assets.json').read_text())
        modules=protocol['development_modules']+protocol['target_modules'];sources={}
        for module in modules:
            relative='bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+module+'.java'
            source=archive/relative;assert digest(source)==manifest[relative];sources[module]=source;freeze(source)
        for p in [Path(__file__).resolve(),protocol_path,ROOT/'scripts/app_db_smoke.py',
                  *[ROOT/path for path in protocol['extractors']]]:freeze(p)
        reference=archive/'bundle/reference.jar';assert digest(reference)==manifest['bundle/reference.jar']
        report['retained_assets'][str(reference)]=digest(reference)
        libraries=sorted(LIB.glob('*.jar'))
        for path in libraries:
            assert digest(path)==retained[str(path)];report['retained_assets'][str(path)]=digest(path)
        save('protocol.json',protocol)
        # This snapshot is persisted before any target AST or graph evaluation.
        save('pre-extraction-snapshot.json',dict(inputs=report['inputs'],retained_assets=report['retained_assets']))
        classes=out/'classes';classes.mkdir()
        extractor=ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'
        run([JDK/'javac','--release','23','-proc:none','-cp',LIB/'json-20240303.jar','-d',classes,extractor],'compile.log')
        cp=':'.join(map(str,[reference,*libraries]))
        for module in modules:
            run([JDK/'java','-cp',str(classes)+':'+str(LIB/'json-20240303.jar'),
                 'research.JdbcBoundaryExtract',out/(module+'-facts.json'),cp,sources[module]],module+'-extract.log')
            facts=json.loads((out/(module+'-facts.json')).read_text())
            graph=recover(facts);save(module+'-graph.json',graph)
            row=summarize(facts,graph)
            row['role']='development' if module in protocol['development_modules'] else 'target'
            report['modules'][module]=row
            print(json.dumps(dict(module=module,**{k:v for k,v in row.items() if k not in ('binding_details',)})),flush=True)
        for path,sha in report['inputs'].items():assert digest(ROOT/path)==sha,('input modified',path)
        for path,sha in report['retained_assets'].items():assert digest(Path(path))==sha
        assert not set(protocol['reserved_modules']) & set(report['modules'])
        report.update(status='completed',algorithm_modified=False,labels_read=False,
                      interpretation='Completed means the fixed coverage measurement ran, not that transfer succeeded. No target variants, behavioral labels or model scores were produced.')
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('report.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)


if __name__=='__main__':main()
