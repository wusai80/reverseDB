#!/usr/bin/env python3
"""Five-arm fixed E2E diagnostic of guarded value feature limits.

Each arm compiles its own Java helper and installs its paired SQL.
"""
import json
import os
import shutil
import subprocess
import time
import uuid
import sys
from pathlib import Path
from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from test_name_lookup_pruning import TABLES, SCENARIOS
from generate_neworder_fusion import emit, adapter_variant, HELPER
from generate_payment_segments import generate

sys.path.insert(0,str(ROOT/'prototype/module_pruning'))
from guarded_value_variants import variants

ARMS = ('original', 'full', 'old_value', 'alternative_producer', 'aligned_empty')
CORRECT = ('full', 'old_value', 'alternative_producer')


def main():
    out=ROOT/'evidence/guarded-value-e2e'/('20261106-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-guardvalue-'+out.name; resources=[]
    report=dict(status='running',commands=[],inputs={},retained_assets={},cases={},cleanup_errors=[])
    env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='0')
    def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(cmd,log,stdin=None,check=True,timeout=180,with_code=False):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return (p.returncode,p.stdout.decode()) if with_code else p.stdout.decode()
    def retain(archive,relative):
        p=archive/relative; assert digest(p)==json.loads((archive/'manifest.json').read_text())[relative]
        report['retained_assets'][str(p)]=digest(p); return p
    def freeze(p):
        dst=out/'inputs'/p.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
    def sql(query,log,database='experiment'):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log).strip()
    def state(folder,phase):
        result={}
        for table in TABLES:
            query=f"""SELECT json_build_object('rows',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) x"""
            result[table]=json.loads(sql(query,folder+'/'+phase+'-'+table+'.log'))
        save(out/folder/(phase+'-state.json'),result); return result
    try:
        bundle=ARCHIVE/'bundle'; scenarios=ARCHIVE/'run/inputs/app/tpcc/scenarios'
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=retain(obs,'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java')
        source=retain(ARCHIVE,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/Payment.java')
        ir=retain(ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013','transaction-ir.json')
        facts=retain(ROOT/'evidence/payment-fusion-transfer/20261105-735301a5f9db','extracted.json')
        invariant=retain(ARCHIVE,'run/inputs/prototype/tpcc/invariants.sql')
        for p in [bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class')),*sorted(scenarios.glob('*'))]:
            retain(ARCHIVE,str(p.relative_to(ARCHIVE)))
        dump=ROOT/'work/tpcc/runs/c615d3357989/initial.dump'; retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for p in [dump,*sorted(LIB.glob('*.jar'))]:
            assert digest(p)==retained[str(p)]; report['retained_assets'][str(p)]=digest(p)
        tracer=ROOT/'app/tpcc/src/reversedb/tpcc/JdbcTrace.java'; trace_test=ROOT/'app/tpcc/test/TraceClockBoundaryTest.java'
        lock=ROOT/'work/paperless/images.lock.json'
        for p in [Path(__file__).resolve(),ROOT/'scripts/test_order_e2e.py',
          ROOT/'prototype/module_pruning/payment_order_variants.py',ROOT/'prototype/module_pruning/real_order_features.py',
          ROOT/'prototype/module_pruning/guarded_value_variants.py',ROOT/'prototype/module_pruning/guarded_value_features.py',
          tracer,trace_test,lock,adapter,source,ir,facts,invariant,
          *[ROOT/'scripts'/f for f in ('generate_neworder_fusion.py','generate_payment_segments.py','fusion_regions.py','fusion_placement.py','fusion_placement_contracts.py','payment_fusion_transfer.py','app_db_smoke.py','test_name_lookup_pruning.py')]]: freeze(p)
        generated=out/'generated'; generated.mkdir()
        payment=generate(source.read_text(),adapter.read_text(),json.loads(facts.read_text()))
        combined=adapter_variant(payment['adapter'])
        assert combined.count('return FusedNewOrder.execute(c,cmd);')==1
        assert combined.count('return SegmentedPayment.execute(c,cmd);')==1
        original_bridge=adapter_variant(adapter.read_text())
        archived_bridge=retain(ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013','generated/TpccApp.java')
        assert original_bridge==archived_bridge.read_text()
        nsql,_=emit(json.loads(ir.read_text()),'full')
        source_archive=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'
        assert nsql==retain(source_archive,'generated/full.sql').read_text()
        assert payment['sql']==retain(ROOT/'evidence/payment-segments/20261105-707221611457','generated/install.sql').read_text()
        contrast=variants(payment['helper'],payment['sql'])
        bodies=dict(original=contrast['bodies']['full'],**contrast['bodies'])
        payment_ddls=dict(original=payment['sql'],**contrast['ddls'])
        ddls={arm:'CREATE SCHEMA reversedb_fusion;\n'+nsql+'\n'+ddl for arm,ddl in payment_ddls.items()}
        save(out/'preexecution-features.json',{k:v for k,v in contrast.items() if k not in ('ddls','bodies','helpers')})
        for arm,ddl in ddls.items(): (generated/(arm+'-install.sql')).write_text(ddl)
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); support=str(bundle/'candidate-classes')
        cp=str(bundle/'reference.jar')+':'+support+':'+libs
        runtime=out/'runtime-classes'; runtime.mkdir(); empty=out/'empty'; empty.mkdir()
        classes_by_arm={}
        for arm,helper in contrast['helpers'].items():
            sources=generated/arm; sources.mkdir()
            (sources/'TpccApp.java').write_text(combined)
            (sources/'FusedNewOrder.java').write_text(HELPER)
            (sources/'SegmentedPayment.java').write_text(helper)
            classes=out/(arm+'-classes'); classes.mkdir(); classes_by_arm[arm]=classes
            run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,*sorted(sources.glob('*.java'))],'compile-'+arm+'.log')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',support+':'+libs,'-d',runtime,tracer,trace_test],'compile-tracer.log')
        run([JDK/'java','-cp',str(runtime)+':'+support+':'+libs,'reversedb.tpcc.TraceClockBoundaryTest'],'trace-unit.log')
        tests={key:scenarios/(key+'.json') for key in SCENARIOS}
        expected=dict(SCENARIOS); expected['time_boundaries']=(6,1,1)
        def cmd(id,type,clock=1762300800123,**kw):
            return dict(id=id,type=type,terminal=len(commands)%4+1,clock_ms=clock,warehouse=1,**kw)
        commands=[]; infinity=9223372036825200000
        lines=[dict(item=i,quantity=q) for i,q in [(1,2),(1,3),(2,1),(3,1),(4,1)]]
        commands.append(cmd('edge-new-normal','NewOrder',district=1,customer=1,lines=lines))
        commands.append(cmd('edge-pay-gc','Payment',district=1,customer_district=2,customer=1,amount=.1))
        commands.append(cmd('edge-pay-bc-infinity','Payment',infinity,district=1,customer_district=2,customer=2,amount=.1))
        commands.append(cmd('edge-new-infinity','NewOrder',infinity,district=2,customer=2,lines=lines))
        commands.append(cmd('edge-read-after-commit','OrderStatus',infinity,district=2,customer=2))
        commands.append(cmd('edge-pay-gc-infinity','Payment',infinity,district=1,customer_district=2,customer=1,amount=.1))
        commands.append(cmd('edge-new-abort','NewOrder',infinity,district=2,customer=2,lines=lines[:4]+[dict(item=2147483647,quantity=1)]))
        commands.append(cmd('edge-reject-clock','Payment',-1,district=1,customer_district=2,customer=1,amount=.1))
        tests['time_boundaries']=out/'time-boundaries.json'; save(tests['time_boundaries'],dict(version=1,commands=commands))
        frozen_time=retain(ROOT/'evidence/fusion-e2e/20261105-3516c48063a9','time-boundaries.json')
        assert json.loads(frozen_time.read_text())==json.loads(tests['time_boundaries'].read_text())
        fixture="UPDATE customer SET c_credit='GC' WHERE c_w_id=1 AND c_d_id=2 AND c_id=1; UPDATE customer SET c_credit='BC' WHERE c_w_id=1 AND c_d_id=2 AND c_id=2;"
        (out/'time-boundaries.sql').write_text(fixture+'\n')
        save(out/'protocol.json',dict(arms=list(ARMS),entry='com.oltpbenchmark.api.TpccApp.main with actual AppRuntime validation, observation, commit and rollback',
          scenarios={k:dict(commands=sum(v),committed=v[0],aborted=v[1],rejected=v[2]) for k,v in expected.items()},commands_per_arm=70,
          order='frozen JSON order, four terminal connections executed serially',
          checks=['complete ordered responses','per-command before/after observations','all nine tables after process exit on independent SQL connection','invariants','commit/rollback trace','both generated helpers loaded','installed Payment finish body exactly matches the selected SQL'],
          scope='Payment existing value, alternate producer and aligned wrong value; same NewOrder fusion; full data, fixed UTC, SERIALIZABLE, WAL/fsync/synchronous_commit',
          outcome='Full and two valid-value variants must match; aligned_empty must expose a persistent customer difference despite identical guarded features. Unknown features remain unknown.',
          stop='70 fixed requests per arm, 350 total. No tuning, timing, parser changes or sample expansion'))
        image=json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']; report['image']=image
        run(DOCKER+['volume','create','--label','reversedb.experiment=guarded-value-e2e',name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=guarded-value-e2e','--memory=1536m','--cpus=2',
          '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],
          '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log').strip(); assert port.startswith('127.0.0.1:') and '\n' not in port
        url=f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true'
        golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231'
        for scenario,path in tests.items():
            inputs=json.loads(path.read_text())['commands']; by_id={c['id']:c for c in inputs}
            for arm in ARMS:
                folder=scenario+'_'+arm; (out/folder).mkdir()
                sql('CREATE DATABASE experiment',folder+'/create.log','tpcc')
                with dump.open('rb') as stream: run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],folder+'/restore.log',stdin=stream)
                if scenario=='delivery_edges': sql((scenarios/'delivery_edges.sql').read_text(),folder+'/fixture.log')
                if scenario=='time_boundaries': sql(fixture,folder+'/fixture.log')
                sql(ddls[arm],folder+'/install.log'); state(folder,'initial')
                installed=json.loads(sql("SELECT to_json(prosrc) FROM pg_proc WHERE oid='reversedb_payment.finish_payment(integer,integer,integer,integer,double precision,double precision,integer,text,boolean,timestamp without time zone,double precision,text)'::regprocedure",folder+'/function-body.log'))
                assert installed==bodies[arm], ('wrong installed body',arm)
                gate=json.loads(sql("SELECT json_build_object('fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'),'timezone',current_setting('timezone'))",folder+'/gate.log'))
                assert gate==dict(fsync='on',synchronous_commit='on',full_page_writes='on',timezone='UTC')
                selected=bundle/'reference-adapter' if arm=='original' else classes_by_arm[arm]
                exit_code,app_output=run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2',f'-Xlog:class+load=info:file={out/folder/"class-load.log"}',
                  '-cp',str(selected)+':'+str(runtime)+':'+cp,'com.oltpbenchmark.api.TpccApp',url,'tpcc',path,out/folder,'reference'],folder+'/app.log',with_code=True)
                state(folder,'final'); inv=json.loads(sql(invariant.read_text(),folder+'/invariants.log')); assert len(inv)==5 and not any(inv.values())
                responses=json.loads((out/folder/'responses.json').read_text()); trace=json.loads((out/folder/'jdbc-trace.json').read_text())
                assert [r['id'] for r in responses]==[c['id'] for c in inputs[:len(responses)]]
                counts=tuple(sum(r.get('status')==s for r in responses) for s in ('committed','aborted','rejected'))
                assert len(responses)==len(inputs) and counts==expected[scenario]
                calls=[]
                for row in trace:
                    r=next(r for r in responses if r['id']==row['id']); command=by_id[row['id']]
                    assert [e['op'] for e in row['events'] if e['op'] in ('commit','rollback')]==['commit' if r.get('status')=='committed' else 'rollback']
                    n=sum(e['op'].startswith('execute') for e in row['events'])
                    if arm!='original' and r.get('status')=='committed' and command['type'] in ('NewOrder','Payment'):
                        credit=r['result']['customer']['c_credit'] if command['type']=='Payment' else None
                        expected_calls=1 if command['type']=='NewOrder' else dict(full={'BC':4,'GC':3},old_value={'BC':5,'GC':4},alternative_producer={'BC':4,'GC':4},aligned_empty={'BC':3,'GC':3})[arm][credit]
                        assert n==expected_calls,(folder,row['id'],n,expected_calls)
                    calls.append(dict(id=r['id'],type=command['type'],status=r.get('status','failed'),execute_calls=n))
                loads=(out/folder/'class-load.log').read_text()
                assert str(selected) in next(x for x in loads.splitlines() if 'com.oltpbenchmark.api.TpccApp source:' in x)
                assert str(runtime) in next(x for x in loads.splitlines() if 'reversedb.tpcc.JdbcTrace source:' in x)
                for type,helper in [('NewOrder','FusedNewOrder'),('Payment','SegmentedPayment')]:
                    used=arm!='original' and any(r.get('status')!='rejected' and by_id[r['id']]['type']==type for r in responses)
                    assert ('com.oltpbenchmark.api.'+helper+' source:' in loads)==used
                if scenario=='time_boundaries':
                    witness=json.loads(sql("SELECT json_build_object('infinite_orders',(SELECT count(*) FROM oorder WHERE o_entry_d='infinity'::timestamp),'infinite_payments',(SELECT count(*) FROM history WHERE h_date='infinity'::timestamp))",folder+'/infinity.log'))
                    assert witness==dict(infinite_orders=1,infinite_payments=2)
                comparisons={}
                for file in ('responses.json','observations.json','initial-state.json','final-state.json'):
                    actual=json.loads((out/folder/file).read_text())
                    if arm!='original':
                        reference=json.loads((out/(scenario+'_original')/file).read_text())
                        comparisons[file]=dict(equal=actual==reference,complete_replay_comparable=True)
                        if isinstance(actual,dict):
                            comparisons[file]['different_keys']=[k for k in sorted(set(actual)|set(reference)) if actual.get(k)!=reference.get(k)]
                        elif isinstance(actual,list):
                            comparisons[file]['different_indices']=[i for i,(a,b) in enumerate(zip(actual,reference)) if a!=b]
                            comparisons[file]['lengths']=[len(reference),len(actual)]
                        if arm in CORRECT or file=='initial-state.json': assert actual==reference,(scenario,arm,file)
                    elif scenario in SCENARIOS:
                        old=retain(golden,scenario+'_original/'+file); assert actual==json.loads(old.read_text()),('original regression',scenario,file)
                save(out/folder/'comparison.json',comparisons)
                report['cases'][folder]=dict(status='completed',
                    process_exit_code=exit_code,attempted_requests=len(responses),completed_requests=sum(counts),
                    failed_request=None,
                    unexecuted_ids=[c['id'] for c in inputs[len(responses):]],
                    counts=dict(zip(('committed','aborted','rejected'),counts)),calls=calls,invariants=inv,comparisons=comparisons)
                sql('DROP DATABASE experiment',folder+'/drop.log','tpcc')
                print('Completed fixed application replay: '+folder,flush=True)
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        wrong=[c for key,c in report['cases'].items() if key.endswith('_aligned_empty')]
        assert any(not c['comparisons']['final-state.json']['equal'] for c in wrong),'Missing persistent wrong-value counterexample'
        labels={}
        for arm in ARMS[1:]:
            cases={key:c for key,c in report['cases'].items() if key.endswith('_'+arm)}
            labels[arm]=dict(observed_suite_difference=int(any(not v['equal'] for c in cases.values() for v in c['comparisons'].values())),
                            process_failure_scenarios=[key for key,c in cases.items() if c['process_exit_code']],
                            response_difference_scenarios=[key for key,c in cases.items() if not c['comparisons']['responses.json']['equal']],
                            final_state_difference_scenarios=[key for key,c in cases.items() if not c['process_exit_code'] and not c['comparisons']['final-state.json']['equal']],
                            all_inputs_equivalent=None)
        save(out/'candidate-labels.json',labels)
        assert all(labels[arm]['observed_suite_difference']==0 for arm in CORRECT)
        assert labels['aligned_empty']['observed_suite_difference']==1
        report.update(status='passed',scheduled_requests=350,attempted_requests=sum(c['attempted_requests'] for c in report['cases'].values()),
          completed_requests=sum(c['completed_requests'] for c in report['cases'].values()),commands_per_arm=70,scenarios=5,trace_checks=9,no_timing=True,labels=labels,
          conclusion='Existing-value and alternative-producer candidates match while the extractor remains unknown; aligned wrong value differs despite identical guarded features. No probability calibration.')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container': run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report); save(out/'manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete end-to-end experiment')


if __name__=='__main__': main()
