#!/usr/bin/env python3
"""Bounded second-transaction transfer plus real Java boundary witnesses."""
import copy
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from app_db_smoke import ROOT, ARCHIVE, LIB, JDK, digest
from fusion_regions import sql_effect, plan_neworder
from payment_fusion_transfer import extract, plan, require_complete_region


def main():
    out=ROOT/'evidence/payment-fusion-transfer'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},retained_assets={},commands=[],database_runs=0,timing_samples=0)
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def require(value,message):
        if not value: raise AssertionError(message)
    def check(name,value): require(value,name); report['checks'].append(name)
    def rejects(action):
        try: action()
        except ValueError: return True
        return False
    def run(args,log):
        args=list(map(str,args)); report['commands'].append(args)
        r=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
        (out/log).write_bytes(r.stdout)
        require(r.returncode==0,'Command failed: '+str(out/log))
    try:
        bundle=ARCHIVE/'bundle'; manifest=json.loads((ARCHIVE/'manifest.json').read_text())
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        source=bundle/'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/Payment.java'
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=obs/'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        require(digest(adapter)==json.loads((obs/'manifest.json').read_text())[str(adapter.relative_to(obs))],'Adapter mismatch')
        assets=[source,bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]
        for p in assets: require(digest(p)==manifest[str(p.relative_to(ARCHIVE))],'Archived asset changed '+str(p))
        for p in LIB.glob('*.jar'): require(digest(p)==retained[str(p)],'Library changed '+str(p)); assets.append(p)
        report['retained_assets']={str(p):digest(p) for p in assets}
        prior=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'
        old_regions=ROOT/'evidence/fusion-regions/20261105-2909d665c433'
        extractor=ROOT/'prototype/module_pruning/DependencyExtract.java'
        probe=ROOT/'prototype/module_pruning/PaymentSemanticsProbe.java'
        inputs=[Path(__file__).resolve(),ROOT/'scripts/payment_fusion_transfer.py',ROOT/'scripts/fusion_regions.py',ROOT/'scripts/app_db_smoke.py',
                source,adapter,extractor,probe,ARCHIVE/'manifest.json',ARCHIVE/'retained-assets.json',obs/'manifest.json',
                prior/'bridge-contract.json',prior/'transaction-ir.json',old_regions/'region-plan.json']
        for archive,paths in [(prior,['bridge-contract.json','transaction-ir.json']),(old_regions,['region-plan.json'])]:
            expected=json.loads((archive/'manifest.json').read_text())
            for name in paths: require(digest(archive/name)==expected[name],'Prior proof input changed')
        for p in inputs:
            target=out/'inputs'/p.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
            report['inputs'][str(p.relative_to(ROOT))]=digest(target)
        save('protocol.json',dict(transaction='Payment',generic_rules='unchanged',
            static_scope='Extract nine app helper sites and keep both credit arms opaque; no backend lowering assumed',
            runtime_cases=['GC balance 10.00 amount 0.01','GC balance 262144.00 amount 0.01','BC amount 0.10 string',
                           'BC c_data UTF-16 boundary','GC warehouse name UTF-16 boundary'],
            runtime='Five actual frozen adapter/Payment executions against strict JDBC doubles; no database/driver encoding/rollback claim',
            stop='One transfer audit and fixed semantic probes; do not whitelist unknowns or rerun performance'))
        classes=out/'classes'; classes.mkdir()
        libs=':'.join(map(str,sorted(LIB.glob('*.jar'))))
        cp=':'.join(map(str,[bundle/'reference-adapter',bundle/'reference.jar',bundle/'candidate-classes']))+':'+libs
        run([JDK/'javac','--release','23','-proc:none','-cp',cp,'-d',classes,extractor,probe],'compile.log')
        run([JDK/'java','-cp',str(classes)+':'+libs,'research.DependencyExtract',out/'extracted.json',cp,prior/'bridge-contract.json',source,adapter],'extract.log')
        facts=json.loads((out/'extracted.json').read_text())
        ir=extract(source.read_text(),adapter.read_text(),facts); save('payment-ir.json',ir)
        graph=plan(ir); save('region-plan.json',graph)
        check('nine source-resolved reflected helper sites',len(ir['call_sites'])==9 and all(s['arguments'] for s in ir['call_sites']))
        check('all ten class SQL definitions recognized without parser changes',len(ir['sql'])==10 and all(sql_effect(s['sql'])['known'] for s in ir['sql'].values()))
        check('ten reachable SQL sites known including final projection',graph['coverage']['sql_sites']==10 and graph['coverage']['sql_effects_known'])
        check('unused name lookup remains in source inventory', 'customerByNameSQL' in ir['sql'] and not any(s['field']=='customerByNameSQL' for s in ir['call_sites']))
        check('both credit arms retained in one opaque phase',next(n for n in graph['nodes'] if n['id']=='credit_branch')['body_known'] is False
              and len(next(n for n in graph['nodes'] if n['id']=='credit_branch')['queries'])==3)
        branch_sites=[s for s in ir['call_sites'] if s['conditions']['unknowns']]
        check('predicate extraction preserves true and false unknown branches',
              {u['branch'] for s in branch_sites for u in s['conditions']['unknowns']}=={True,False})
        check('full Payment fusion rejected',rejects(lambda: require_complete_region(ir)))
        check('only final projection remains structural singleton', [r['nodes'] for r in graph['regions']]==[['result_customer']])
        check('numeric object and string obligations not disguised as json',any('unsupported_value_representation' in b['reasons'] for b in graph['barriers']))
        check('warehouse write-read dependency transfers',any(e['before']=='updateWarehouse' and e['after']=='getWarehouse' and e['kind']=='read_after_write' and e['resource']=='warehouse' for e in graph['edges']))
        check('customer read-before-write dependency transfers',any(e['before']=='getCustomerById' and e['after']=='credit_branch' and e['kind']=='write_after_read' and e['resource']=='customer' for e in graph['edges']))
        check('source revisions rejected',rejects(lambda: extract(source.read_text()+'\n',adapter.read_text(),facts)))
        check('unchanged generic analysis reproduces previous NewOrder graph',plan_neworder(json.loads((prior/'transaction-ir.json').read_text()))==json.loads((old_regions/'region-plan.json').read_text()))
        run([JDK/'javap','-classpath',bundle/'reference.jar','com.oltpbenchmark.benchmarks.tpcc.pojo.Customer'],'customer-fields.log')
        run([JDK/'javap','-c','-classpath',bundle/'reference.jar','com.oltpbenchmark.benchmarks.tpcc.TPCCUtil'],'customer-loader-bytecode.log')
        run([JDK/'java','-Duser.timezone=UTC','-cp',str(classes)+':'+cp,'research.PaymentSemanticsProbe',out/'probe.json',
             bundle/'reference-adapter',bundle/'reference.jar'],'probe.log')
        result=json.loads((out/'probe.json').read_text())
        check('five actual source-runtime boundary probes pass',result['status']=='passed' and result['source_runtime_calls']==5)
        check('rounding example distinguishes harmful and harmless cases',
              [c['different_at_cents'] for c in result['cases'] if c['kind']=='float32_customer_arithmetic']==[False,True])
        check('both actual branch paths executed', {len(c['calls']) for c in result['cases']}=={8,9})
        for p,sha in report['retained_assets'].items(): require(digest(Path(p))==sha,'Retained asset changed')
        report.update(status='passed',checks_passed=len(report['checks']),source_runtime_calls=5,
                      graph_nodes=len(graph['nodes']),graph_edges=len(graph['edges']),
                      full_payment_fusion_admitted=False,
                      conclusion='SQL effects/dependencies transfer unchanged; monetary, object and conditional/string semantics need explicit lowering. Original app retained.')
    except BaseException as e:
        report.update(status='failed',error=repr(e)); raise
    finally:
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
