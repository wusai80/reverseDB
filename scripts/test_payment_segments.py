#!/usr/bin/env python3
"""Two-region Payment: fixed whole-app replay, actual DB boundary cases and faults."""
import difflib
import json
import os
import shutil
import statistics
import subprocess
import time
import uuid
from pathlib import Path
from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from test_name_lookup_pruning import TABLES, SCENARIOS
from generate_payment_segments import generate
from fusion_regions import plan_neworder


def main():
    out=ROOT/'evidence/payment-segments'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-payment-'+out.name; created=[]
    report=dict(status='running',inputs={},retained_assets={},commands=[],cases={},cleanup_errors=[])
    env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='0')
    def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(command,log,stdin=None,check=True,timeout=180):
        command=list(map(str,command)); report['commands'].append(command)
        result=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(result.stdout)
        if check and result.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return result.stdout.decode()
    def freeze(path):
        target=out/'inputs'/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        report['inputs'][str(path.relative_to(ROOT))]=digest(target)
    def sql(query,log,database='experiment'):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log).strip()
    def state(folder,phase):
        result={}
        for table in TABLES:
            query=f"""SELECT json_build_object('rows',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) rows"""
            result[table]=json.loads(sql(query,f'{folder}/{phase}-{table}.log'))
        save(out/folder/(phase+'-state.json'),result); return result
    try:
        bundle=ARCHIVE/'bundle'; manifest=json.loads((ARCHIVE/'manifest.json').read_text()); retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        source=bundle/'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/Payment.java'
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=obs/'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        assert digest(adapter)==json.loads((obs/'manifest.json').read_text())[str(adapter.relative_to(obs))]
        scenarios=ARCHIVE/'run/inputs/app/tpcc/scenarios'; invariant=ARCHIVE/'run/inputs/prototype/tpcc/invariants.sql'
        assets=[source,bundle/'reference.jar',invariant,*scenarios.glob('*'),*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]
        dependencies=[*sorted(LIB.glob('*.jar')),ROOT/'work/tpcc/runs/c615d3357989/initial.dump']
        for p in assets: assert digest(p)==manifest[str(p.relative_to(ARCHIVE))]
        for p in dependencies: assert digest(p)==retained[str(p)]
        report['retained_assets']={str(p):digest(p) for p in assets+dependencies}
        transfer=ROOT/'evidence/payment-fusion-transfer/20261105-735301a5f9db'; tm=json.loads((transfer/'manifest.json').read_text())
        facts=transfer/'extracted.json'; assert digest(facts)==tm['extracted.json']
        lock=ROOT/'work/paperless/images.lock.json'
        probes=[ROOT/'prototype/module_pruning/FusionProbe.java',ROOT/'prototype/module_pruning/PaymentSegmentProbe.java']
        inputs=[Path(__file__).resolve(),source,adapter,facts,lock,invariant,*scenarios.glob('*'),*probes,
                *[ROOT/'scripts'/f for f in ('generate_payment_segments.py','payment_fusion_transfer.py','fusion_regions.py','fusion_placement.py','fusion_placement_contracts.py','generate_neworder_fusion.py','app_db_smoke.py','test_name_lookup_pruning.py')],
                ARCHIVE/'manifest.json',ARCHIVE/'retained-assets.json',obs/'manifest.json',transfer/'manifest.json']
        for p in inputs: freeze(p)
        protocol=dict(arms=['original','segmented'],commands_per_arm=62,scenarios=list(SCENARIOS),
          placement='four warehouse/district SQLs in prefix; original Java customer/arithmetic/credit/C_DATA; customer update/history/result in suffix',
          scope='source-bound manually specified cuts and typed JDBC ABI; no generic Java lowering',
          preserved=['all business data','original float and UTF-16 operations','same connection and outer SERIALIZABLE transaction','WAL/locks/synchronous commit'],
          assumptions=['pinned schema, ordinary tables, no user triggers/rules/RLS','serial normal execution; exact fault timing not preserved','non-null names make local history-text computation safe to move before suffix update'],
          diagnostics=['five previous semantic boundary cases on real PostgreSQL, both arms, full nine-table state','two fault boundaries x two credit branches x two arms'],
          timing=dict(cases=['gc-small','bc-text'],warmups_per_arm_case=3,blocks=10,seed=20261105,scope='helper plus complete result, excludes fixtures/commit/rollback'),
          stop='One fixed replay and bounded diagnostics/timing; no parameter search')
        save(out/'protocol.json',protocol)
        result=generate(source.read_text(),adapter.read_text(),json.loads(facts.read_text()))
        try: generate(source.read_text()+'\n',adapter.read_text(),json.loads(facts.read_text()))
        except ValueError: report['unknown_source_rejected']=True
        else: raise AssertionError('Unknown source admitted')
        old=ROOT/'evidence/fusion-regions/20261105-2909d665c433'; old_ir=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013/transaction-ir.json'
        for archive,path in [(old,old/'region-plan.json'),(old_ir.parent,old_ir)]:
            assert digest(path)==json.loads((archive/'manifest.json').read_text())[str(path.relative_to(archive))]
            freeze(path)
        assert plan_neworder(json.loads(old_ir.read_text()))==json.loads((old/'region-plan.json').read_text())
        report['neworder_graph_unchanged']=True
        generated=out/'generated'; generated.mkdir()
        (generated/'install.sql').write_text(result['sql']); (generated/'SegmentedPayment.java').write_text(result['helper'])
        (generated/'TpccApp.java').write_text(result['adapter']); save(generated/'source-map.json',result['source_map']); save(out/'region-plan.json',result['plan'])
        save(out/'automatic-placement.json',result['automatic_placement'])
        (generated/'adapter.diff').write_text(''.join(difflib.unified_diff(adapter.read_text().splitlines(True),result['adapter'].splitlines(True),fromfile='original/TpccApp.java',tofile='segmented/TpccApp.java')))
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); support=str(bundle/'candidate-classes'); cp=str(bundle/'reference.jar')+':'+support+':'+libs
        classes=out/'segmented-adapter'; classes.mkdir(); probe=out/'probe-classes'; probe.mkdir(); empty=out/'empty-sourcepath'; empty.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,generated/'TpccApp.java',generated/'SegmentedPayment.java'],'compile-candidate.log')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',support+':'+libs,'-d',probe,*probes],'compile-probe.log')
        image=json.loads(lock.read_text())['images']['postgres']; assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']
        report['image']=image
        run(DOCKER+['volume','create','--label','reversedb.experiment=payment-segments',name],'volume.log'); created.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=payment-segments','--memory=1536m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432',
            '-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); created.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log').strip(); assert port.startswith('127.0.0.1:') and '\n' not in port
        url=f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true'
        gate_query="""SELECT json_build_object(
          'unsafe_tables',(SELECT count(*) FROM pg_class WHERE oid IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'history'::regclass) AND (relkind<>'r' OR relrowsecurity OR relforcerowsecurity)),
          'user_triggers',(SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'history'::regclass)),
          'rules',(SELECT count(*) FROM pg_rewrite WHERE ev_class IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'history'::regclass)),
          'nonnull_names',(SELECT count(*) FROM pg_attribute WHERE (attrelid,attname) IN (('warehouse'::regclass,'w_name'),('district'::regclass,'d_name')) AND attnotnull),
          'fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'),'replication_role',current_setting('session_replication_role'))"""
        def prepare(folder,fixture):
            sql('CREATE DATABASE experiment',folder+'/create.log','tpcc')
            with dependencies[-1].open('rb') as stream: run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],folder+'/restore.log',stdin=stream)
            if fixture=='delivery_edges': sql((scenarios/'delivery_edges.sql').read_text(),folder+'/fixture.log')
            gate=json.loads(sql(gate_query,folder+'/gate.log')); save(out/folder/'gate.json',gate)
            assert all(gate[k]==0 for k in ('unsafe_tables','user_triggers','rules')) and gate['nonnull_names']==2
            assert gate['replication_role']=='origin' and all(gate[k]=='on' for k in ('fsync','synchronous_commit','full_page_writes'))
            sql(result['sql'],folder+'/install.log'); sql('ANALYZE',folder+'/analyze.log')
        golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231'; gm=json.loads((golden/'manifest.json').read_text())
        for scenario,expected in SCENARIOS.items():
            commands={c['id']:c for c in json.loads((scenarios/(scenario+'.json')).read_text())['commands']}
            for arm in protocol['arms']:
                folder=scenario+'_'+arm; (out/folder).mkdir(); prepare(folder,scenario); state(folder,'initial')
                adapter_classes=bundle/'reference-adapter' if arm=='original' else classes
                run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2',f'-Xlog:class+load=info:file={out/folder/"class-load.log"}',
                    '-cp',str(adapter_classes)+':'+cp,'com.oltpbenchmark.api.TpccApp',url,'tpcc',scenarios/(scenario+'.json'),out/folder,'reference'],folder+'/replay.log')
                loads=(out/folder/'class-load.log').read_text()
                assert str(adapter_classes) in next(line for line in loads.splitlines() if 'com.oltpbenchmark.api.TpccApp source:' in line)
                responses=json.loads((out/folder/'responses.json').read_text())
                if arm=='segmented':
                    payment_executed=any(r['status']!='rejected' and commands[r['id']]['type']=='Payment' for r in responses)
                    assert ('com.oltpbenchmark.api.SegmentedPayment source:' in loads)==payment_executed
                state(folder,'final'); values=json.loads(sql(invariant.read_text(),folder+'/invariants.log')); save(out/folder/'invariants.json',values)
                assert len(values)==5 and not any(values.values())
                assert [r['id'] for r in responses]==list(commands)
                assert tuple(sum(r['status']==s for r in responses) for s in ('committed','aborted','rejected'))==expected
                trace=json.loads((out/folder/'jdbc-trace.json').read_text()); counts=[]
                for row in trace:
                    response=next(r for r in responses if r['id']==row['id']); status=response['status']
                    assert [e['op'] for e in row['events'] if e['op'] in ('commit','rollback')]==['commit' if status=='committed' else 'rollback']
                    if commands[row['id']]['type']=='Payment':
                        count=sum(e['op'].startswith('execute') for e in row['events']); counts.append(dict(id=row['id'],calls=count))
                        if status=='committed':
                            original_count=9 if response['result']['customer']['c_credit']=='BC' else 8
                            assert count==(original_count if arm=='original' else original_count-5)
                report['cases'][folder]=dict(commands=len(commands),payment_executes=counts)
                for filename in ('responses.json','observations.json','initial-state.json','final-state.json','invariants.json'):
                    previous=golden/(scenario+'_original')/filename; assert digest(previous)==gm[str(previous.relative_to(golden))]
                    assert json.loads((out/folder/filename).read_text())==json.loads(previous.read_text()),(folder,filename)
                    report['retained_assets'][str(previous)]=digest(previous)
                if arm=='segmented':
                    old_trace=json.loads((out/(scenario+'_original')/'jdbc-trace.json').read_text())
                    assert [r for r in trace if commands[r['id']]['type']!='Payment']==[r for r in old_trace if commands[r['id']]['type']!='Payment']
                sql('DROP DATABASE experiment',folder+'/drop.log','tpcc'); print('Verified '+folder,flush=True)
        folder='diagnostics'; (out/folder).mkdir(); prepare(folder,'baseline'); before=state(folder,'initial')
        run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2','-cp',str(probe)+':'+support+':'+libs,
             'research.PaymentSegmentProbe',url,bundle/'reference-adapter',classes,bundle/'reference.jar',out/'probe-results.json'],'probe.log',timeout=240)
        assert state(folder,'final')==before
        results=json.loads((out/'probe-results.json').read_text())
        assert results['status']=='passed' and len(results['checks'])==10 and len(results['faults'])==8 and len(results['samples'])==40
        summaries=[]
        for case in ('gc-small','bc-text'):
            medians={arm:statistics.median(r['ms'] for r in results['samples'] if r['case']==case and r['arm']==arm) for arm in protocol['arms']}
            summaries.append(dict(case=case,median_ms=medians,reduction_percent=100*(1-medians['segmented']/medians['original'])))
        save(out/'timing-summary.json',summaries)
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        report.update(status='passed',semantic_commands=124,database_boundary_executions=10,fault_executions=8,timed_calls=40,summary=summaries,
                      conclusion='Source-bound two-region fusion preserves fixed replay, actual DB type/string witnesses and rollback. Original Java semantics retained; not a general compiler or throughput claim.')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(created):
            try:
                if resource=='container': run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report); save(out/'manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete Payment experiment')


if __name__=='__main__': main()
