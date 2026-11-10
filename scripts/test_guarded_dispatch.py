#!/usr/bin/env python3
"""Bounded dominating-guard test on real NewOrder plus typed Java counterexamples."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from app_db_smoke import ROOT,JDK,LIB,digest
from cross_layer_dependencies import recover,correspond

FACTS=ROOT/'evidence/jdbc-boundary-contracts/20261105-149567965337'
E2E=ROOT/'evidence/fusion-e2e/20261105-3516c48063a9'
NEW=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'


def main():
    out=ROOT/'evidence/guarded-dispatch'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status='running',checks=[],commands=[],inputs={},retained_assets={})
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def freeze(p):
        dst=out/'inputs'/p.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
    def archived(archive,name):
        p=archive/name; assert digest(p)==json.loads((archive/'manifest.json').read_text())[name]
        freeze(p); return p
    def check(name,ok):
        if not ok: raise AssertionError(name)
        report['checks'].append(name)
    def run(cmd,log):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=90)
        (out/log).write_text(p.stdout+p.stderr)
        if p.returncode: raise RuntimeError('Command failed: '+str(out/log))
    try:
        for p in [Path(__file__).resolve(),ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java',ROOT/'scripts/cross_layer_dependencies.py',ROOT/'scripts/jdbc_boundary_contracts.py',ROOT/'scripts/app_db_smoke.py']: freeze(p)
        old=archived(FACTS,'inputs/prototype/module_pruning/JdbcBoundaryExtract.java')
        assert digest(old)==digest(ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'), 'Fresh extraction required'
        check('existing boundary regression passed',json.loads(archived(FACTS,'run.json').read_text())['checks_passed']==22)
        original=json.loads(archived(FACTS,'original.json').read_text()); candidate=json.loads(archived(FACTS,'repaired.json').read_text())
        narrow_ddl=archived(E2E,'generated/install.sql').read_text()
        all_ddl=archived(NEW,'generated/install.sql').read_text()+'\n'+archived(ROOT/'evidence/payment-segments/20261105-707221611457','generated/install.sql').read_text()
        new=next(s for s in candidate['statements'] if 'FusedNewOrder#' in s['receiver'])
        check('actual config target set recovered from its guard',new['status']=='finite_sql_set' and len(new['sql_alternatives'])==4 and len(new['guard_constraints'])==1)
        check('default value does not erase other admitted modes',set(new['guard_constraints'][0]['values'])=={'full','pruned','early_write','full_fault'})
        check('guard certificate has source and dominance evidence',new['guard_constraints'][0]['line']==12 and new['guard_constraints'][0]['rule']=='stable-string-rejecting-set-guard/v1')
        ref=recover(original); sparse=recover(candidate,narrow_ddl); graph=recover(candidate,all_ddl)
        save('deployed-catalog-graph.json',sparse); save('all-archived-targets-graph.json',graph); save('correspondence.json',correspond(ref,graph))
        check('missing bodies are not removed by target narrowing',len(sparse['paths'])==3 and len(sparse['unknowns'])==3 and all(x['reason']=='missing or overloaded function' for x in sparse['unknowns']))
        check('all archived targets retain six may-write paths',len(graph['paths'])==6 and not graph['unknowns'])
        order=next(x for x in correspond(ref,graph) if x['sink']=='oorder.o_entry_d')
        check('NewOrder slot correspondence inferred for all four guarded alternatives',order['original_slot']==5 and len(order['candidates'])==4 and all(c['jdbc_slot']==5 and c['formal']=='p_time' for c in order['candidates']))
        check('graph retains resolved guard and dynamic selection as separate controls',sum(n['kind']=='finite_target_guard' for n in graph['nodes'])==4 and any(n['kind']=='unresolved_dispatch' for n in graph['nodes']))
        save('protocol.json',dict(scope='stable local String narrowed by preceding direct rejecting Set.of(...).contains guard',
          real_case='unchanged FusedNewOrder config selection; all four modes, not just default',
          no_codegen_or_workload_change=True,closed=['finite syntactic target set after supported guard'],
          still_open=['selected member at runtime','method/body availability','procedure effects and path feasibility','external state and SQLStmt premises'],
          stop='fixed typed-AST guard controls and two catalog views; no DB or timing'))
        init='String mode=System.getProperty("test.mode","alpha");'
        test='!java.util.Set.of("alpha","beta").contains(mode)'
        guard='if('+test+') throw new IllegalArgumentException();'
        use='try(PreparedStatement stmt=c.prepareStatement("SELECT demo_"+mode+"(?)")){ stmt.setTimestamp(1,new Timestamp(cmd.getLong("clock_ms"))); stmt.execute(); }'
        cases={
          'direct':(init+guard+use,2),
          'block_throw':(init+'if('+test+'){throw new IllegalArgumentException();}'+use,2),
          'renamed':((init+guard+use).replace('mode','selection'),2),
          'three_values':(init+guard.replace('"alpha","beta"','"alpha","beta","gamma"')+use,3),
          'outer_guard_nested_use':(init+guard+'{'+use+'}',2),
          'unguarded':(init+use,0),
          'late_guard':(init+use+guard,0),
          'non_rejecting':(init+'if('+test+') System.out.println("bad");'+use,0),
          'conditional_guard':(init+'if(cmd.getBoolean("check")){'+guard+'}'+use,0),
          'caught_then_resume':(init+'try{'+guard+'}catch(IllegalArgumentException ignored){}'+use,0),
          'different_variable':(init+'String other=System.getProperty("other");'+guard.replace('contains(mode)','contains(other)')+use,0),
          'reassigned_after':(init+guard+'mode="gamma";'+use,0),
          'reassigned_before':(init+'mode=System.getProperty("other");'+guard+use,0),
          'opposite_condition':(init+guard.replace('!java.util','java.util')+use,0),
          'mutable_set':(init+'java.util.Set<String> allowed=new java.util.HashSet<>(java.util.Set.of("alpha","beta")); if(!allowed.contains(mode)) throw new IllegalArgumentException();'+use,0),
          'fake_contains':(init+'if(!new Pretend().contains(mode)) throw new IllegalArgumentException();'+use,0),
          'lambda_guard':(init+'Runnable validator=()->{'+guard+'}; validator.run();'+use,0),
          'loop_guard':(init+'while(cmd.getBoolean("check")){'+guard+'break;}'+use,0),
          'return_reject':(init+'if('+test+') return;'+use,0),
          'nonliteral_allowlist':(init+'if(!java.util.Set.of("alpha",cmd.getString("allowed")).contains(mode)) throw new IllegalArgumentException();'+use,0),
        }
        source='package research; import java.sql.*; import org.json.*; public class GuardExamples { static class Pretend { boolean contains(String value){return true;} }\n'
        for name,(body,_) in cases.items(): source+='static void '+name+'(Connection c,JSONObject cmd) throws Exception {'+body+'}\n'
        source+='}\n'; fixture=out/'GuardExamples.java'; fixture.write_text(source)
        classes=out/'extractor-classes'; classes.mkdir()
        jar=LIB/'json-20240303.jar'; prior=json.loads((FACTS/'run.json').read_text()); assert digest(jar)==prior['retained_assets'][str(jar)]; report['retained_assets'][str(jar)]=digest(jar)
        run([JDK/'javac','--release','23','-proc:none','-cp',jar,'-d',classes,ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'],'compile.log')
        run([JDK/'java','-cp',str(classes)+':'+str(jar),'research.JdbcBoundaryExtract',out/'controls.json',jar,fixture],'extract.log')
        controls=json.loads((out/'controls.json').read_text()); summary=[]
        for name,(_,count) in cases.items():
            records=[s for s in controls['statements'] if s['receiver'].startswith('research.GuardExamples#'+name+'(')]
            check(name+' yields only justified SQL alternatives',len(records)==1 and len(records[0]['sql_alternatives'])==count)
            check(name+' records matching guard status',bool(records[0]['guard_constraints'])==bool(count))
            summary.append(dict(case=name,status=records[0]['status'],alternatives=records[0]['sql_alternatives'],guards=records[0]['guard_constraints']))
        save('control-summary.json',summary)
        report.update(status='passed',checks_passed=len(report['checks']),controls=len(cases),neworder_targets=4,
          complete_catalog_paths=len(graph['paths']),deployed_catalog_unknowns=len(sparse['unknowns']),new_database_runs=0,new_timing_samples=0,
          conclusion='Guard bounds recovered from typed AST under restricted block dominance and immutable string binding; all alternatives retained, no execution equivalence or deletion proof')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
