#!/usr/bin/env python3
"""Check static labels against executed Java targets and PostgreSQL catalogs."""
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from app_db_smoke import ROOT,DOCKER,JDK,LIB,ARCHIVE,digest
from cross_layer_dependencies import recover

GUARD=ROOT/'evidence/guarded-dispatch/20261105-2ef8f8b49ccc'
FACTS=ROOT/'evidence/jdbc-boundary-contracts/20261105-149567965337'
E2E=ROOT/'evidence/fusion-e2e/20261105-3516c48063a9'
NEW=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'


def main():
    out=ROOT/'evidence/dispatch-label-validation'/('20261106-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-labels-'+out.name; created=False
    report=dict(status='running',checks=[],commands=[],inputs={},retained_assets={},cleanup_errors=[])
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok: raise AssertionError(name)
        report['checks'].append(name)
    def freeze(p):
        dst=out/'inputs'/p.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst); report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
    def archived(archive,name):
        p=archive/name; assert digest(p)==json.loads((archive/'manifest.json').read_text())[name]; freeze(p); return p
    def run(cmd,log,check=True,timeout=90):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return p.stdout.decode()
    def sql(query,log):
        return run(DOCKER+['exec',name,'psql','-U','research','-d','research','-XAt','-v','ON_ERROR_STOP=1','-c',query],log).strip()
    try:
        harness=ROOT/'prototype/module_pruning/DispatchLabelProbe.java'; extractor=ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'
        lock=ROOT/'work/paperless/images.lock.json'
        for p in [Path(__file__).resolve(),harness,extractor,lock,ROOT/'scripts/cross_layer_dependencies.py',ROOT/'scripts/jdbc_boundary_contracts.py',ROOT/'scripts/app_db_smoke.py']: freeze(p)
        fixture=archived(GUARD,'GuardExamples.java').read_text()
        # Two new controls independently challenge bypass through finally/catch.
        init='String mode=System.getProperty("test.mode","alpha");'
        guard='if(!java.util.Set.of("alpha","beta").contains(mode)) throw new IllegalArgumentException();'
        use='try(PreparedStatement s=c.prepareStatement("SELECT demo_"+mode+"(?)")){s.setTimestamp(1,new Timestamp(cmd.getLong("clock_ms")));s.execute();}'
        extra='static void finally_bypass(Connection c,JSONObject cmd) throws Exception {'+init+'try{'+guard+'}finally{'+use+'}}\n'
        extra+='static void caught_inside_reject(Connection c,JSONObject cmd) throws Exception {'+init+'if(!java.util.Set.of("alpha","beta").contains(mode)){try{throw new IllegalArgumentException();}catch(IllegalArgumentException ignored){}}'+use+'}\n'
        fixture=fixture[:fixture.rfind('}')]+extra+'}\n'; source=out/'GuardExamples.java'; source.write_text(fixture)
        helper=archived(E2E,'generated/FusedNewOrder.java')
        candidate=json.loads(archived(FACTS,'repaired.json').read_text())
        frozen_extractor=archived(FACTS,'inputs/prototype/module_pruning/JdbcBoundaryExtract.java')
        assert digest(extractor)==digest(frozen_extractor)
        deployed=archived(E2E,'generated/install.sql').read_text()
        additional=archived(NEW,'generated/install.sql').read_text()
        payment=archived(ROOT/'evidence/payment-segments/20261105-707221611457','generated/install.sql').read_text()
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        bundle=ARCHIVE/'bundle'; manifest=json.loads((ARCHIVE/'manifest.json').read_text())
        for p in [bundle/'reference.jar',*sorted((bundle/'candidate-classes').rglob('*.class'))]:
            assert digest(p)==manifest[str(p.relative_to(ARCHIVE))]; report['retained_assets'][str(p)]=digest(p)
        for p in LIB.glob('*.jar'):
            assert digest(p)==retained[str(p)]; report['retained_assets'][str(p)]=digest(p)
        save('protocol.json',dict(java_cases='7 actual NewOrder configs plus 22 guard methods x 7 fixed profiles',
          oracle='actual Java control flow stopped at prepareStatement; pg_proc entries after installing actual function DDL',
          primary_property='every observed SQL target of a finite label belongs to its static candidate set',
          unknown_policy='unknown does not mean unreachable or unsafe; record observed behavior without promoting finite testing into a proof',
          catalogs=['deployed E2E definitions','deployed definitions plus archived NewOrder definitions'],
          stop='161 Java executions and two catalog snapshots; no business data, timing, or optimization changes'))
        classes=out/'classes'; classes.mkdir(); empty=out/'empty'; empty.mkdir()
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); cp=str(bundle/'reference.jar')+':'+str(bundle/'candidate-classes')+':'+libs
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,helper,source,harness,extractor],'compile.log')
        run([JDK/'java','-cp',str(classes)+':'+cp,'research.DispatchLabelProbe',out/'observed.json'],'runtime.log')
        run([JDK/'java','-cp',str(classes)+':'+cp,'research.JdbcBoundaryExtract',out/'control-facts.json',cp,source],'extract.log')
        observed=json.loads((out/'observed.json').read_text()); controls=json.loads((out/'control-facts.json').read_text())
        actual=next(s for s in candidate['statements'] if 'FusedNewOrder#' in s['receiver'])
        seen={sql for row in observed['actual'] for sql in row['prepared_sql']}
        check('all four statically labeled real targets are observed',seen==set(actual['sql_alternatives']))
        check('default reaches full while invalid configs stop before JDBC',len(observed['actual'])==7 and all((row['outcome']=='reached_jdbc')==(row['mode'] in (None,'full','pruned','early_write','full_fault')) for row in observed['actual']))
        check('unknown real modes throw the guard exception',all(row['exception']=='java.lang.IllegalArgumentException' for row in observed['actual'] if row['outcome']=='threw_before_jdbc'))
        rows=[]
        for spec in controls['statements']:
            method=spec['receiver'].split('#',1)[1].split('(',1)[0]
            executions=[r for r in observed['controls'] if r['method']==method]; targets={s for r in executions for s in r['prepared_sql']}
            check(method+' fixed execution coverage',len(executions)==7)
            finite=spec['status']=='finite_sql_set'
            if finite:
                check(method+' has no observed omitted target',targets<=set(spec['sql_alternatives']))
                check(method+' all labeled alternatives exercised',targets==set(spec['sql_alternatives']))
            rows.append(dict(method=method,label=spec['status'],static_targets=spec['sql_alternatives'],observed_targets=sorted(targets),
              reached=sum(r['outcome']=='reached_jdbc' for r in executions),inference='tested target coverage only' if finite else 'unknown retained; no unreachable/safe inference'))
        save('label-audit.json',rows)
        check('all 154 control executions recorded',len(observed['controls'])==154 and len(rows)==22)
        check('new finally and caught-throw bypasses remain unknown and can reach outside targets',all(next(r for r in rows if r['method']==m)['label']=='unknown' and 'SELECT demo_gamma(?)' in next(r for r in rows if r['method']==m)['observed_targets'] for m in ('finally_bypass','caught_inside_reject')))
        check('unknown does not imply unreachable',all(r['reached']>0 for r in rows if r['label']=='unknown'))
        check('unsupported return/lambda guards remain conservative abstentions',all(next(r for r in rows if r['method']==m)['label']=='unknown' and set(next(r for r in rows if r['method']==m)['observed_targets'])=={'SELECT demo_alpha(?)','SELECT demo_beta(?)'} for m in ('return_reject','lambda_guard')))
        image=json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']; report['image']=image
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=dispatch-labels','--memory=384m','--cpus=1',
          '--tmpfs','/var/lib/postgresql:rw,size=256m','-e','POSTGRES_USER=research','-e','POSTGRES_PASSWORD=local-fixture-only','-e','POSTGRES_DB=research',image['resolved']], 'start.log'); created=True
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','research'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Readiness timeout')
        catalog_query="SELECT coalesce(json_agg(n.nspname||'.'||p.proname ORDER BY n.nspname,p.proname),'[]'::json) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('reversedb_fusion','reversedb_payment')"
        sql(deployed,'install-deployed.log')
        catalogs=[]
        # Analyze a definition snapshot, not the sequential CREATE OR REPLACE
        # installation history (which would duplicate the full function text).
        for phase,ddl in [('deployed',deployed),('all_archived',additional+'\n'+payment)]:
            if phase=='all_archived': sql(additional.replace('CREATE SCHEMA reversedb_fusion;','CREATE SCHEMA IF NOT EXISTS reversedb_fusion;'),'install-additional.log')
            names=set(json.loads(sql(catalog_query,phase+'-catalog.log')))
            graph=recover(candidate,ddl)
            targets={re.match(r'SELECT ([\w.]+)\(',s).group(1) for spec in candidate['statements'] if 'FusedNewOrder#' in spec['receiver'] for s in spec['sql_alternatives']}
            absent=targets-names
            marked={re.match(r'SELECT ([\w.]+)\(',u['sql']).group(1) for u in graph['unknowns'] if u['reason']=='missing or overloaded function'}
            check(phase+' missing-body labels match real PostgreSQL catalog',marked==absent)
            check(phase+' expected missing counts',len(absent)==(3 if phase=='deployed' else 0))
            check(phase+' graphs never authorize deletion',not graph['automatic_deletion_authorized'])
            catalogs.append(dict(phase=phase,postgres_functions=sorted(names),known_targets=sorted(targets),missing_targets=sorted(absent),marked_missing=sorted(marked),paths=len(graph['paths'])))
        save('catalog-audit.json',catalogs)
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        report.update(status='passed',checks_passed=len(report['checks']),java_executions=161,
          finite_control_labels=sum(r['label']=='finite_sql_set' for r in rows),unknown_control_labels=sum(r['label']=='unknown' for r in rows),
          observed_omissions=0,catalog_snapshots=2,business_transactions=0,
          conclusion='No false finite target exclusion found in fixed execution matrix; missing bodies agree with PostgreSQL; unknown labels are conservative and are not deletion labels')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        if created:
            try:
                run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup.log')
            except Exception as error: report['cleanup_errors'].append(repr(error)); report['status']='failed'
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))),flush=True)


if __name__=='__main__': main()
