#!/usr/bin/env python3
"""Bounded paired performance experiment, full application and durable commits."""
import difflib
import json
import os
import shutil
import statistics
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from test_name_lookup_pruning import TABLES


def instrument(runtime, tracer):
    """Benchmark-only copies: business timer and compact JDBC events, same clock ABI."""
    def replace(text, old, new):
        assert text.count(old)==1, old
        return text.replace(old,new)
    runtime=replace(runtime,'long started=System.nanoTime();',
        'execution.put("id",cmd.getString("id")).put("type",cmd.getString("type"));\n        runtime.put(execution);\n        long started=System.nanoTime();')
    runtime=replace(runtime,'          runtime.put(execution);','')
    runtime=replace(runtime,'if(observe) execution.put("finished_at"','execution.put("finished_at"')
    runtime=replace(runtime,'if(observe) Files.writeString(out.resolve("runtime.json")','Files.writeString(out.resolve("runtime.json")')
    tracer=replace(tracer,'static final boolean OBSERVE="1".equals(System.getenv("TPCC_OBSERVE"));','static final boolean OBSERVE=true;')
    start=tracer.index('          boolean recorded =')
    end=tracer.index('          long started=OBSERVE',start)
    tracer=tracer[:start]+'''          boolean recorded = name.startsWith("execute");
          JSONObject event = recorded ? new JSONObject().put("op",name) : null;
          if(recorded) trace.put(event);
'''+tracer[end:]
    tracer=replace(tracer,'if (result instanceof ResultSet rs) return rows(rs, trace);','// No per-row/getter event allocation in performance runs.')
    tracer=replace(tracer,'          if (recorded && name.startsWith("execute")) event.put("result", value(result));','')
    tracer=replace(tracer,'          Object result = call(target, method, args);\n          if (method.getName().equals("prepareStatement"))',
        '          long started=System.nanoTime();\n          Object result = call(target, method, args);\n          long duration=System.nanoTime()-started;\n          if (method.getName().equals("prepareStatement"))')
    tracer=replace(tracer,'trace.put(new JSONObject().put("op", method.getName()));',
        'trace.put(new JSONObject().put("op", method.getName()).put("duration_ns",duration));')
    return runtime,tracer


def summarize(cases):
    measured=[c for c in cases if c['phase']=='measurement']
    result={}
    for group in ['all','NewOrder','Payment','OrderStatus','Delivery','StockLevel']:
        arms={}
        for arm in ('original','combined'):
            runs=[c for c in measured if c['arm']==arm]
            totals=[sum(s['duration_ns'] for s in c['samples'] if group=='all' or s['type']==group)/1e6 for c in runs]
            calls=[sum(s['execute_calls'] for s in c['samples'] if group=='all' or s['type']==group) for c in runs]
            ends=[sum(s['end_ns'] for s in c['samples'] if group=='all' or s['type']==group)/1e6 for c in runs]
            arms[arm]=dict(total_ms_by_round=totals,median_total_ms=statistics.median(totals),
                           min_total_ms=min(totals),max_total_ms=max(totals),execute_calls_by_round=calls,
                           end_ms_by_round=ends,median_end_ms=statistics.median(ends),
                           transactions_per_round=sum(group=='all' or s['type']==group for s in runs[0]['samples']))
        a,b=arms['original']['total_ms_by_round'],arms['combined']['total_ms_by_round']
        result[group]=dict(arms=arms,ratio_of_medians=statistics.median(a)/statistics.median(b),
            reduction_pct=100*(1-statistics.median(b)/statistics.median(a)),
            paired_speedups=[x/y for x,y in zip(a,b)],median_paired_speedup=statistics.median(x/y for x,y in zip(a,b)))
    return result


def main():
    out=ROOT/'evidence/fusion-performance'/(datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-perf-'+out.name; resources=[]; process=None; jlog=None
    report=dict(status='running',commands=[],inputs={},retained_assets={},cases=[],cleanup_errors=[])
    env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='0')
    def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(cmd,log,stdin=None,check=True,timeout=180):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return p.stdout.decode().strip()
    def retain(archive,relative):
        p=archive/relative; assert digest(p)==json.loads((archive/'manifest.json').read_text())[relative],p
        report['retained_assets'][str(p)]=digest(p); return p
    def freeze(p):
        dst=out/'inputs'/p.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
    def sql(query,log,database='experiment'):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)
    def state(folder,phase):
        result={}
        for table in TABLES:
            query=f"""SELECT json_build_object('rows',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) x"""
            result[table]=json.loads(sql(query,folder+'/'+phase+'-'+table+'.log'))
        save(out/folder/(phase+'-state.json'),result); return result
    try:
        bundle=ARCHIVE/'bundle'
        scenario=retain(ARCHIVE,'run/inputs/app/tpcc/scenarios/holdout.json')
        invariant=retain(ARCHIVE,'run/inputs/prototype/tpcc/invariants.sql')
        for p in [bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]:
            retain(ARCHIVE,str(p.relative_to(ARCHIVE)))
        dump=ROOT/'work/tpcc/runs/c615d3357989/initial.dump'; retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for p in [dump,*sorted(LIB.glob('*.jar'))]:
            assert digest(p)==retained[str(p)]; report['retained_assets'][str(p)]=digest(p)
        safe=ROOT/'evidence/guarded-value-e2e/20261106-2b184bc5dc73'
        generated=out/'generated'; generated.mkdir()
        for file in ('TpccApp.java','FusedNewOrder.java','SegmentedPayment.java'):
            shutil.copy2(retain(safe,'generated/full/'+file),generated/file)
        ddl=retain(safe,'generated/full-install.sql'); shutil.copy2(ddl,generated/'install.sql')
        runtime=ROOT/'app/tpcc/src/reversedb/tpcc/AppRuntime.java'; tracer=ROOT/'app/tpcc/src/reversedb/tpcc/JdbcTrace.java'
        probe=ROOT/'prototype/module_pruning/FusionPerformanceProbe.java'; lock=ROOT/'work/paperless/images.lock.json'
        for p in (Path(__file__).resolve(),ROOT/'scripts/app_db_smoke.py',ROOT/'scripts/test_name_lookup_pruning.py',runtime,tracer,probe,lock): freeze(p)
        instrumented=instrument(runtime.read_text(),tracer.read_text())
        for source,text in zip((runtime,tracer),instrumented):
            (generated/source.name).write_text(text)
            (generated/(source.stem+'.diff')).write_text(''.join(difflib.unified_diff(source.read_text().splitlines(True),text.splitlines(True),fromfile=str(source),tofile=str(generated/source.name))))
        protocol=dict(arms=['original','combined'],workload='frozen holdout: 17 commands, 10 commits, 3 aborts, 4 input rejections',
            warmup_pairs=2,measured_pairs=6,order='AB,BA alternating across all eight pairs; exact frozen command order',
            isolation='SERIALIZABLE; four terminal connections executed serially',
            reset='full 599051-row dump restored per run; both arms install same functions; ANALYZE, full initial hash and CHECKPOINT outside timer',
            timing='business executor + result construction + commit/rollback and compact instrumentation; excludes validation, connection establishment, before/after observations, restore, hash, JVM startup',
            instrumentation='same runtime for both arms; clock substitution preserved; execute count/time and commit/rollback time; no per-row or parameter value logging',
            jit='one persistent JVM; separate persistent adapter class loaders; fresh DB connections per replay; two warmups per arm, not proof of fully warmed JIT',
            stop='exactly two warmup and six measurement pairs, no sample expansion',
            inference='paired short serial latency experiment; not standard TPC-C, concurrent throughput, or statistical significance')
        save(out/'protocol.json',protocol)
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); support=str(bundle/'candidate-classes')
        cp=str(bundle/'reference.jar')+':'+support+':'+libs
        classes=out/'combined-classes'; classes.mkdir(); rtclasses=out/'runtime-classes'; rtclasses.mkdir(); empty=out/'empty'; empty.mkdir()
        compiler=[JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty]
        run(compiler+['-cp',cp,'-d',classes,*[generated/f for f in ('TpccApp.java','FusedNewOrder.java','SegmentedPayment.java')]],'compile-combined.log')
        run(compiler+['-cp',support+':'+libs,'-d',rtclasses,generated/'AppRuntime.java',generated/'JdbcTrace.java',probe],'compile-runtime.log')
        image=json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id']; report['image']=image
        run([JDK/'java','-version'],'java-version.log'); run(DOCKER+['version'],'docker-version.log')
        run(DOCKER+['volume','create','--label','reversedb.experiment=fusion-performance',name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=fusion-performance','--memory=1536m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log'); assert port.startswith('127.0.0.1:') and '\n' not in port
        url=f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true'
        save(out/'settings.json',json.loads(sql("SELECT json_object_agg(name,setting) FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes','wal_level','autovacuum','shared_buffers','server_version','timezone')",'settings.log','tpcc')))
        run(DOCKER+['exec',name,'df','-T','/var/lib/postgresql'],'filesystem.log')
        jcmd=list(map(str,[JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2',f'-Xlog:class+load=info:file={out/"class-load.log"}',
            '-cp',str(rtclasses)+':'+support+':'+libs,'research.FusionPerformanceProbe',bundle/'reference-adapter',classes,bundle/'reference.jar']))
        report['commands'].append(jcmd); jlog=(out/'jvm.log').open('w')
        process=subprocess.Popen(jcmd,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=jlog,stderr=subprocess.STDOUT,text=True)
        golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231'
        expected={file:json.loads(retain(golden,'holdout_original/'+file).read_text()) for file in ('responses.json','observations.json','initial-state.json','final-state.json')}
        for pair in range(8):
            phase='warmup' if pair<2 else 'measurement'; round_number=pair if pair<2 else pair-2
            for arm in (('original','combined') if pair%2==0 else ('combined','original')):
                folder=f'{phase}{round_number}_{arm}'; target=out/folder; target.mkdir()
                sql('CREATE DATABASE experiment',folder+'/create.log','tpcc')
                with dump.open('rb') as stream: run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],folder+'/restore.log',stdin=stream)
                sql(ddl.read_text(),folder+'/install.log'); sql('ANALYZE',folder+'/analyze.log')
                initial=state(folder,'initial'); assert sum(v['rows'] for v in initial.values())==599051
                sql('CHECKPOINT',folder+'/checkpoint.log')
                request=dict(arm=arm,url=url,commands=str(scenario),output=str(target)); save(target/'request.json',request)
                process.stdin.write(json.dumps(request)+'\n'); process.stdin.flush()
                deadline=time.monotonic()+90
                while not (target/'complete').exists():
                    if process.poll() is not None: raise RuntimeError('JVM failed: '+str(out/'jvm.log'))
                    if time.monotonic()>deadline: raise TimeoutError(folder)
                    time.sleep(.05)
                state(folder,'final')
                inv=json.loads(sql(invariant.read_text(),folder+'/invariants.log')); assert len(inv)==5 and not any(inv.values())
                for file,old in expected.items(): assert json.loads((target/file).read_text())==old,(folder,file)
                responses=json.loads((target/'responses.json').read_text())
                counts={status:sum(r['status']==status for r in responses) for status in ('committed','aborted','rejected')}
                assert counts==dict(committed=10,aborted=3,rejected=4)
                timing=json.loads((target/'runtime.json').read_text()); traces=json.loads((target/'jdbc-trace.json').read_text())
                assert len(timing)==len(traces)==13
                assert [r['id'] for r in timing]==[r['id'] for r in responses if r['status']!='rejected']
                samples=[]
                for sample,trace in zip(timing,traces):
                    assert sample['id']==trace['id']
                    ends=[e for e in trace['events'] if e['op'] in ('commit','rollback')]
                    assert [e['op'] for e in ends]==['commit' if sample['status']=='committed' else 'rollback']
                    executes=[e for e in trace['events'] if e['op'].startswith('execute')]
                    execute_ns=sum(e['duration_ns'] for e in executes); end_ns=ends[0]['duration_ns']
                    assert sample['duration_ns']>=execute_ns+end_ns>0
                    if arm=='combined' and sample['status']=='committed' and sample['type'] in ('NewOrder','Payment'):
                        response=next(r for r in responses if r['id']==sample['id'])
                        assert len(executes)==(1 if sample['type']=='NewOrder' else 4 if response['result']['customer']['c_credit']=='BC' else 3)
                    samples.append(dict(id=sample['id'],type=sample['type'],status=sample['status'],duration_ns=sample['duration_ns'],execute_calls=len(executes),execute_ns=execute_ns,end_ns=end_ns))
                report['cases'].append(dict(phase=phase,round=round_number,arm=arm,counts=counts,samples=samples,correctness='complete frozen responses, observations, nine-table initial/final hashes and five invariants match'))
                sql('DROP DATABASE experiment',folder+'/drop.log','tpcc')
                print(f'Verified {folder}: business+end {sum(s["duration_ns"] for s in samples)/1e6:.2f} ms',flush=True)
        process.stdin.close(); assert process.wait(timeout=20)==0
        loads=(out/'class-load.log').read_text()
        for cls in ('AppRuntime','JdbcTrace'):
            assert str(rtclasses) in next(l for l in loads.splitlines() if f'reversedb.tpcc.{cls} source:' in l)
        for cls in ('FusedNewOrder','SegmentedPayment'):
            assert str(classes) in next(l for l in loads.splitlines() if f'com.oltpbenchmark.api.{cls} source:' in l)
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        save(out/'summary.json',summarize(report['cases']))
        report.update(status='passed',application_commands=272,measured_commands=204,measured_accepted_transactions=156)
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=10)
        if jlog is not None: jlog.close()
        for resource in reversed(resources):
            try:
                if resource=='container': run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report)
        manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
        save(out/'manifest.json',manifest)
        assert all(digest(out/p)==sha for p,sha in manifest.items())
        print(json.dumps(dict(status=report['status'],output=str(out),manifest_files=len(manifest))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete benchmark')


if __name__=='__main__': main()
