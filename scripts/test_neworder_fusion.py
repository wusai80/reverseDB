#!/usr/bin/env python3
"""Source-bound NewOrder fusion: original / fused / fused+one proven pruning."""
import copy
import difflib
import hashlib
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
from generate_neworder_fusion import lower, emit, adapter_variant, HELPER
from fusion_regions import plan_neworder


def main():
    out=ROOT/'evidence/neworder-fusion'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-fusion-'+out.name
    report=dict(status='running',inputs={},retained_assets={},commands=[],cases={},cleanup_errors=[])
    created=[]; env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='0')
    def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(command,log,stdin=None,check=True,timeout=180):
        command=list(map(str,command)); report['commands'].append(command)
        result=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(result.stdout)
        if check and result.returncode: raise RuntimeError(f'Command failed: {out/log}')
        return result.stdout.decode()
    def freeze(path):
        target=out/'inputs'/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        report['inputs'][str(path.relative_to(ROOT))]=digest(target)
        return target
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
        source=bundle/'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/NewOrder.java'
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=obs/'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        om=json.loads((obs/'manifest.json').read_text()); assert digest(adapter)==om[str(adapter.relative_to(obs))]
        scenarios=ARCHIVE/'run/inputs/app/tpcc/scenarios'; invariant=ARCHIVE/'run/inputs/prototype/tpcc/invariants.sql'
        archived=[source,bundle/'reference.jar',invariant,*scenarios.glob('*'),*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]
        dependencies=[*sorted(LIB.glob('*.jar')),ROOT/'work/tpcc/runs/c615d3357989/initial.dump']
        for path in archived: assert digest(path)==manifest[str(path.relative_to(ARCHIVE))]
        for path in dependencies: assert digest(path)==retained[str(path)]
        for path in archived+dependencies: report['retained_assets'][str(path)]=digest(path)
        extractor=ROOT/'prototype/module_pruning/DependencyExtract.java'
        harness=ROOT/'prototype/module_pruning/FusionProbe.java'; lock=ROOT/'work/paperless/images.lock.json'
        inputs=[Path(__file__).resolve(),ROOT/'scripts/generate_neworder_fusion.py',ROOT/'scripts/fusion_regions.py',
                ROOT/'scripts/fusion_placement.py',ROOT/'scripts/fusion_placement_contracts.py',ROOT/'scripts/payment_fusion_transfer.py',ROOT/'scripts/app_db_smoke.py',ROOT/'scripts/test_name_lookup_pruning.py',
                source,adapter,extractor,harness,lock,invariant,*scenarios.glob('*'),ARCHIVE/'manifest.json',ARCHIVE/'retained-assets.json',obs/'manifest.json']
        for path in inputs: freeze(path)
        protocol=dict(arms=['original','full','pruned'],root='unchanged AppRuntime input contract and TpccApp NewOrder branch',
            source='Hash-bound Vineflower-recompiled NewOrder plus frozen adapter; attributed SQL/call/binding extraction',
            lowering='Handwritten restricted backend rules consume source-derived IR; not a general compiler or automatic theorem discovery',
            placement='Generated PL/pgSQL runs inside PostgreSQL; one outer JDBC execute per NewOrder, not zero JDBC or removal of SQL executor',
            preserved=['all legal existing application inputs and all data','complete results','customer/order/stock checks in full arm','source line order and deferred stock writes','outer SERIALIZABLE commit/rollback','WAL and synchronous commit'],
            preconditions=['frozen ordinary public tables without user triggers/rules/RLS','same fixed serial profile','normal SQL completion except specified invalid-item and injected late failure controls','validated enforced nondeferrable FK chain for pruned arm'],
            excluded=['concurrency/fault timing and exact diagnostic logs or SQL trace identity','arbitrary external reflection or alternate SQL dialects','general source revisions'],
            scenarios=list(SCENARIOS),commands_per_arm=62,
            timing=dict(cases=['new-01','new-04','new-05'],warmups_per_arm=3,blocks=10,seed=20261105,scope='rollback-only transaction helper plus complete results, excludes commit; same JVM and database'),
            negative_controls=['early stock write changes duplicate-item state','failure after all writes must roll back'],
            stop='One three-arm semantic run and fixed timing probe; no parameter search or expanded workload')
        save(out/'protocol.json',protocol)
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); support=str(bundle/'candidate-classes')
        cp=str(bundle/'reference.jar')+':'+support+':'+libs
        compiler=out/'extractor-classes'; compiler.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-cp',LIB/'json-20240303.jar','-d',compiler,extractor],'compile-extractor.log')
        bridge_text=adapter.read_text(); method=bridge_text[bridge_text.index('  static Object invoke('):bridge_text.index('  static JSONObject reference(')].strip()
        bridge=dict(symbol='com.oltpbenchmark.api.TpccApp#invoke(java.lang.Object,java.lang.String,java.lang.Object...)',method_sha256=hashlib.sha256(method.encode()).hexdigest())
        save(out/'bridge-contract.json',bridge)
        run([JDK/'java','-cp',str(compiler)+':'+str(LIB/'json-20240303.jar'),'research.DependencyExtract',out/'extracted.json',cp,out/'bridge-contract.json',source,adapter],'extract.log')
        facts=json.loads((out/'extracted.json').read_text())
        ir=lower(source.read_text(),adapter.read_text(),facts,digest(source),digest(adapter)); save(out/'transaction-ir.json',ir)
        save(out/'fusion-region-plan.json',plan_neworder(ir))
        try: lower(source.read_text().replace('>= 10','>= 11'),adapter.read_text(),facts,digest(source),digest(adapter))
        except ValueError: report['unknown_source_rejected']=True
        else: raise AssertionError('Unknown source accepted')
        bad=copy.deepcopy(facts)
        for call in bad['calls']:
            if call['expression']=='stmtInsertOrderLine.executeBatch()': call['expression']='stmtUpdateStock.executeBatch()'
        try: lower(source.read_text(),adapter.read_text(),bad,digest(source),digest(adapter))
        except AssertionError: report['incompatible_batch_order_rejected']=True
        else: raise AssertionError('Incompatible batch order accepted')
        generated=out/'generated'; generated.mkdir()
        definitions=['CREATE SCHEMA reversedb_fusion;']
        for mode in ('full','pruned','early_write','full_fault'):
            text,mapping=emit(ir,mode); (generated/(mode+'.sql')).write_text(text); save(generated/(mode+'-source-map.json'),mapping); definitions.append(text)
        ddl='\n'.join(definitions); (generated/'install.sql').write_text(ddl)
        fused_adapter=adapter_variant(adapter.read_text()); (generated/'TpccApp.java').write_text(fused_adapter); (generated/'FusedNewOrder.java').write_text(HELPER)
        (generated/'adapter.diff').write_text(''.join(difflib.unified_diff(adapter.read_text().splitlines(True),fused_adapter.splitlines(True),fromfile='original/TpccApp.java',tofile='fused/TpccApp.java')))
        classes=out/'fused-adapter'; classes.mkdir(); empty=out/'empty-sourcepath'; empty.mkdir(); probe=out/'probe-classes'; probe.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,generated/'TpccApp.java',generated/'FusedNewOrder.java'],'compile-fused.log')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',support+':'+libs,'-d',probe,harness],'compile-probe.log')
        image=json.loads(lock.read_text())['images']['postgres']; assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']
        report['image']=image
        run(DOCKER+['volume','create','--label','reversedb.experiment=neworder-fusion',name],'create-volume.log'); created.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=neworder-fusion','--memory=1536m','--cpus=2',
             '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432',
             '-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],
             '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); created.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Postgres readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log').strip(); assert port.startswith('127.0.0.1:') and '\n' not in port
        url=f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true'
        gate_query="""SELECT json_build_object(
          'unsafe_tables',(SELECT count(*) FROM pg_class WHERE oid IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'item'::regclass,'stock'::regclass,'oorder'::regclass,'new_order'::regclass,'order_line'::regclass) AND (relkind<>'r' OR relrowsecurity OR relforcerowsecurity)),
          'user_triggers',(SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'item'::regclass,'stock'::regclass,'oorder'::regclass,'new_order'::regclass,'order_line'::regclass)),
          'rules',(SELECT count(*) FROM pg_rewrite WHERE ev_class IN ('customer'::regclass,'district'::regclass,'warehouse'::regclass,'item'::regclass,'stock'::regclass,'oorder'::regclass,'new_order'::regclass,'order_line'::regclass)),
          'foreign_keys',(SELECT json_agg(x) FROM (SELECT conrelid::regclass::text child,pg_get_constraintdef(oid) definition,convalidated,condeferrable,conenforced FROM pg_constraint WHERE contype='f' AND conrelid IN ('customer'::regclass,'district'::regclass) ORDER BY conname) x),
          'disabled_fk_triggers',(SELECT count(*) FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint WHERE c.contype='f' AND c.conrelid IN ('customer'::regclass,'district'::regclass) AND t.tgenabled NOT IN ('O','A')),
          'timestamp_columns',(SELECT json_agg(x) FROM (SELECT attrelid::regclass::text relation,attname FROM pg_attribute WHERE attrelid IN ('oorder'::regclass,'order_line'::regclass) AND atttypid='timestamp'::regtype AND attnum>0 AND NOT attisdropped ORDER BY attrelid::regclass::text,attname) x),
          'replication_role',current_setting('session_replication_role'),'fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'))"""
        def prepare(folder,fixture):
            sql('CREATE DATABASE experiment',folder+'/create.log','tpcc')
            with dependencies[-1].open('rb') as stream:
                run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],folder+'/restore.log',stdin=stream)
            if fixture=='delivery_edges': sql((scenarios/'delivery_edges.sql').read_text(),folder+'/fixture.log')
            gate=json.loads(sql(gate_query,folder+'/gate.log')); save(out/folder/'gate.json',gate)
            assert all(gate[k]==0 for k in ('unsafe_tables','user_triggers','rules','disabled_fk_triggers'))
            assert gate['replication_role']=='origin' and all(gate[k]=='on' for k in ('fsync','synchronous_commit','full_page_writes'))
            keys=gate['foreign_keys']; assert len(keys)==2 and all(k['convalidated'] and k['conenforced'] and not k['condeferrable'] for k in keys)
            assert {k['definition'] for k in keys}=={'FOREIGN KEY (c_w_id, c_d_id) REFERENCES district(d_w_id, d_id) ON DELETE CASCADE','FOREIGN KEY (d_w_id) REFERENCES warehouse(w_id) ON DELETE CASCADE'}
            assert {(r['relation'],r['attname']) for r in gate['timestamp_columns']}=={('oorder','o_entry_d'),('order_line','ol_delivery_d')}
            sql(ddl,folder+'/install.log'); sql('ANALYZE',folder+'/analyze.log')
        golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231'; gm=json.loads((golden/'manifest.json').read_text())
        for scenario,expected in SCENARIOS.items():
            command_list=json.loads((scenarios/(scenario+'.json')).read_text())['commands']; commands={c['id']:c for c in command_list}
            for arm in protocol['arms']:
                folder=scenario+'_'+arm; (out/folder).mkdir(); prepare(folder,scenario); state(folder,'initial')
                adapter_classes=bundle/'reference-adapter' if arm=='original' else classes
                run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2','-Dreversedb.fusion.mode='+arm,
                     f'-Xlog:class+load=info:file={out/folder/"class-load.log"}',
                     '-cp',str(adapter_classes)+':'+cp,'com.oltpbenchmark.api.TpccApp',url,'tpcc',scenarios/(scenario+'.json'),out/folder,'reference'],folder+'/replay.log')
                loads=(out/folder/'class-load.log').read_text()
                assert str(adapter_classes) in next(line for line in loads.splitlines() if 'com.oltpbenchmark.api.TpccApp source:' in line)
                if arm!='original':
                    assert 'com.oltpbenchmark.api.FusedNewOrder source:' in loads
                    assert 'com.oltpbenchmark.benchmarks.tpcc.procedures.NewOrder source:' not in loads
                state(folder,'final'); values=json.loads(sql(invariant.read_text(),folder+'/invariants.log')); save(out/folder/'invariants.json',values)
                assert len(values)==5 and not any(values.values())
                responses=json.loads((out/folder/'responses.json').read_text()); assert [r['id'] for r in responses]==list(commands)
                assert tuple(sum(r['status']==status for r in responses) for status in ('committed','aborted','rejected'))==expected
                trace=json.loads((out/folder/'jdbc-trace.json').read_text()); counts=[]
                for row in trace:
                    if commands[row['id']]['type']=='NewOrder':
                        count=sum(e['op'].startswith('execute') for e in row['events']); counts.append(dict(id=row['id'],calls=count))
                        if arm!='original': assert count==1
                    boundaries=[e['op'] for e in row['events'] if e['op'] in ('commit','rollback')]
                    status=next(r['status'] for r in responses if r['id']==row['id'])
                    assert boundaries==['commit' if status=='committed' else 'rollback']
                report['cases'][folder]=dict(commands=len(commands),neworder_executes=counts)
                for filename in ('responses.json','observations.json','initial-state.json','final-state.json','invariants.json'):
                    previous=golden/(scenario+'_original')/filename; assert digest(previous)==gm[str(previous.relative_to(golden))]
                    assert json.loads((out/folder/filename).read_text())==json.loads(previous.read_text()),(folder,filename)
                    report['retained_assets'][str(previous)]=digest(previous)
                if arm!='original':
                    old=json.loads((out/(scenario+'_original')/'jdbc-trace.json').read_text())
                    assert [row for row in trace if commands[row['id']]['type']!='NewOrder']==[row for row in old if commands[row['id']]['type']!='NewOrder']
                sql('DROP DATABASE experiment',folder+'/drop.log','tpcc'); print('Verified '+folder,flush=True)
        folder='diagnostics'; (out/folder).mkdir(); prepare(folder,'baseline'); before=state(folder,'initial')
        run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2','-cp',str(probe)+':'+support+':'+libs,
             'research.FusionProbe',url,bundle/'reference-adapter',classes,bundle/'reference.jar',scenarios/'holdout.json',out/'probe-results.json'],'probe.log',timeout=240)
        assert state(folder,'final')==before
        results=json.loads((out/'probe-results.json').read_text()); assert results['status']=='passed' and len(results['samples'])==90
        summaries=[]
        for case in protocol['timing']['cases']:
            medians={arm:statistics.median(row['ms'] for row in results['samples'] if row['id']==case and row['arm']==arm) for arm in protocol['arms']}
            summaries.append(dict(case=case,median_ms=medians,fusion_reduction_percent=100*(1-medians['full']/medians['original']),extra_pruning_reduction_percent=100*(1-medians['pruned']/medians['full'])))
        save(out/'timing-summary.json',summaries)
        for path,sha in report['retained_assets'].items(): assert digest(Path(path))==sha
        report.update(status='passed',semantic_commands=186,timed_calls=90,summary=summaries,
                      conclusion='Source-bound cross-layer generated fusion preserves frozen application observations, reduces NewOrder to one outer execute, and distinguishes wrong stage order; not a general compiler or throughput claim')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(created):
            try:
                if resource=='container':
                    run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report); save(out/'manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete fusion experiment')


if __name__=='__main__': main()
