#!/usr/bin/env python3
"""One fixed AST contract-recovery experiment; no new DB executions or timing."""
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from app_db_smoke import ROOT, JDK, LIB, ARCHIVE, digest
from jdbc_boundary_contracts import inspect_binding, compare


def main():
    out=ROOT/'evidence/jdbc-boundary-contracts'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status='running',checks=[],commands=[],inputs={},retained_assets={})
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,value):
        if not value: raise AssertionError(name)
        report['checks'].append(name)
    def run(cmd,log):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=90)
        (out/log).write_text(p.stdout+p.stderr)
        if p.returncode: raise RuntimeError('Command failed: '+str(out/log))
    def retain(archive,relative):
        p=archive/relative; manifest=json.loads((archive/'manifest.json').read_text())
        assert digest(p)==manifest[relative]; report['retained_assets'][str(p)]=digest(p); return p
    def extract(label,paths):
        run([JDK/'java','-cp',str(classes)+':'+str(LIB/'json-20240303.jar'),'research.JdbcBoundaryExtract',out/(label+'.json'),cp,*paths],label+'.log')
        return json.loads((out/(label+'.json')).read_text())
    def binding(facts,type,method,slot):
        owners={x['owner'] for x in facts['bindings'] if x['owner'].startswith(type+'#'+method+'(')}
        if len(owners)!=1: raise AssertionError('Method correspondence ambiguous '+type+'#'+method)
        return inspect_binding(facts,owners.pop(),slot)
    try:
        bundle=ARCHIVE/'bundle'
        original=[retain(ARCHIVE,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+n+'.java') for n in ('NewOrder','Payment')]
        fixed=ROOT/'evidence/timestamp-abi/20261105-f803874e717c'
        repaired=[retain(fixed,'generated/new-fixed/FusedNewOrder.java'),retain(fixed,'generated/payment-fixed/SegmentedPayment.java')]
        old=[retain(ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013','generated/FusedNewOrder.java'),
             retain(ROOT/'evidence/payment-segments/20261105-707221611457','generated/SegmentedPayment.java')]
        trace=retain(ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc','run/inputs/app/tpcc/src/reversedb/tpcc/JdbcTrace.java')
        runtime=retain(ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc','run/inputs/app/tpcc/src/reversedb/tpcc/AppRuntime.java')
        prior=retain(fixed,'probe-results.json')
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for jar in LIB.glob('*.jar'):
            assert digest(jar)==retained[str(jar)]; report['retained_assets'][str(jar)]=digest(jar)
        for f in [bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]:
            retain(ARCHIVE,str(f.relative_to(ARCHIVE)))
        extractor=ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'
        for p in [Path(__file__).resolve(),ROOT/'scripts/jdbc_boundary_contracts.py',ROOT/'scripts/app_db_smoke.py',extractor,*original,*repaired,*old,trace,runtime,prior]:
            dst=out/'inputs'/p.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
            report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
        save('protocol.json',dict(scope='recover selected JDBC boundary expressions with attributed Java AST, check under frozen runtime/caller premises',
          inputs='two original time writes, two repaired helpers, two historical helpers, fixed structural controls',
          domain='all nonnegative Long command clocks under unchanged validation; no data-derived restriction',
          manual_premises=['hash-bound JdbcTrace timestamp substitution','original/candidate SQL destination and slot correspondence','same command and driver','no external alias mutation before execution'],
          excluded=['whole-program control/heap analysis','SQL effect equivalence','automatic lifting of all placement guarantees','new runtime or performance tests'],
          stop='fixed AST extraction and controls; unknowns retained'))
        classes=out/'extractor-classes'; classes.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-cp',LIB/'json-20240303.jar','-d',classes,extractor],'compile.log')
        cp=':'.join(map(str,[bundle/'reference.jar',bundle/'reference-adapter',bundle/'candidate-classes',*sorted(LIB.glob('*.jar'))]))
        ref=extract('original',original); current=extract('repaired',repaired); historical=extract('historical',old)
        pairs=[('NewOrder','insertOpenOrder',5,'FusedNewOrder',5),('Payment','insertHistory',6,'SegmentedPayment',10)]
        certificates=[]; references=[]
        for name,method,slot,helper,target in pairs:
            r=binding(ref,'com.oltpbenchmark.benchmarks.tpcc.procedures.'+name,method,slot); references.append(r)
            check(name+' original JDBC binding extracted',r['status']=='extracted')
            for label,facts in [('repaired',current),('historical',historical)]:
                c=binding(facts,'com.oltpbenchmark.api.'+helper,'execute',target); result=compare(r,c)
                check(name+' '+label+' structural verdict',result['status']==('matched_under_premises' if label=='repaired' else 'different_structure'))
                certificates.append(dict(program=name,version=label,reference=r,candidate=c,comparison=result,
                  runtime_premise_sha256=digest(trace),validation_premise_sha256=digest(runtime),
                  mapped_source_slot=slot,mapped_candidate_slot=target))
        save('binding-comparisons.json',certificates)
        witness=json.loads(prior.read_text())
        check('historical structural differences have archived real database witnesses',len(witness['negative_controls'])==3 and all(x['sql_state']=='22008' for x in witness['negative_controls']))
        timestamp='new Timestamp(cmd.getLong("clock_ms"))'
        baseline='s.setTimestamp(5,'+timestamp+');'
        cases={
          'direct':(baseline,'matched_under_premises'),
          'renamed':('s.setTimestamp(5,new java.sql.Timestamp(cmd.getLong("clock_ms")));','matched_under_premises'),
          'immutable_alias':('long ticks=cmd.getLong("clock_ms"); s.setTimestamp(5,new Timestamp(ticks));','matched_under_premises'),
          'string_codec':('s.setString(5,'+timestamp+'.toString());','different_structure'),
          'wrong_key':('s.setTimestamp(5,new Timestamp(cmd.getLong("other_clock")));','different_structure'),
          'wall_clock':('s.setTimestamp(5,new Timestamp(System.currentTimeMillis()));','different_structure'),
          'comment_decoy':('/* '+baseline+' */ s.setString(5,'+timestamp+'.toString());','different_structure'),
          'conditional':('if(cmd.getBoolean("enabled")) { '+baseline+' }','unknown'),
          'duplicate':(baseline+baseline,'unknown'),
          'reassigned':('long ticks=cmd.getLong("clock_ms"); ticks=0; s.setTimestamp(5,new Timestamp(ticks));','unknown'),
          'dynamic_index':('int index=cmd.getInt("slot"); '+baseline+' s.setTimestamp(index,'+timestamp+');','unknown'),
          'calendar_overload':('s.setTimestamp(5,'+timestamp+',java.util.Calendar.getInstance());','unknown'),
          'clear_bindings':(baseline+' s.clearParameters();','unknown'),
          'unsupported_value':('s.setTimestamp(5,Timestamp.valueOf("2026-11-05 00:00:00"));','unknown'),
        }
        text='package research; import java.sql.*; import org.json.*; public class BoundaryExamples {\n'
        for name,(body,_) in cases.items():
            text+='static void '+name+'(Connection connection,JSONObject cmd) throws Exception { try(PreparedStatement s=connection.prepareStatement("SELECT ?")) { '+body+' s.execute(); } }\n'
        text+='}\n'; fixture=out/'BoundaryExamples.java'; fixture.write_text(text)
        facts=extract('controls',[fixture]); controls=[]
        for name,(_,expected) in cases.items():
            b=binding(facts,'research.BoundaryExamples',name,5); verdict=compare(references[0],b)
            check('control '+name,verdict['status']==expected); controls.append(dict(name=name,binding=b,verdict=verdict))
        save('control-verdicts.json',controls)
        stale=dict(references[0],value={'op':'timestamp','value':{'op':'literal','value':0}})
        check('changed reference cannot reuse frozen-clock rule',compare(stale,binding(current,'com.oltpbenchmark.api.FusedNewOrder','execute',5))['status']=='unknown')
        for path,sha in report['retained_assets'].items(): assert digest(Path(path))==sha
        report.update(status='passed',checks_passed=len(report['checks']),structural_controls=len(cases),
          conditional_boundary_matches=2,historical_differences=2,new_database_executions=0,new_timing_samples=0,
          conclusion='Two selected binding obligations recovered and checked from typed AST under explicit runtime/alias/SQL correspondence premises; no global equivalence or automatic module deletion claim')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
