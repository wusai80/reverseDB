#!/usr/bin/env python3
"""One warmup plus three fixed, serial kernel-cost observations on a disk volume."""
import collections
import difflib
import json
import os
import shutil
import statistics
import subprocess
import uuid
from pathlib import Path

from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from test_name_lookup_pruning import TABLES


def instrument(runtime, trace):
    anchor = '        raw.setReadOnly(readOnly);\n'
    assert runtime.count(anchor) == 1
    runtime = runtime.replace(anchor, anchor + '        try (Statement diagnostic = raw.createStatement()) { diagnostic.execute("SET LOCAL pg_stat_statements.track = \'top\'"); }\n')
    anchor = '''          Object result = call(target, method, args);
          if (method.getName().equals("prepareStatement"))
            return statement((PreparedStatement) result, (String) args[0], millis, trace, freezeClock);
          if (method.getName().equals("commit") || method.getName().equals("rollback"))
            trace.put(new JSONObject().put("op", method.getName()));
          return result;'''
    replacement = '''          boolean boundary = method.getName().equals("commit") || method.getName().equals("rollback");
          long started = boundary ? System.nanoTime() : 0;
          Object result;
          try { result = call(target, method, args); }
          finally { if (boundary) trace.put(new JSONObject().put("op", method.getName()).put("duration_ns", System.nanoTime()-started)); }
          if (method.getName().equals("prepareStatement"))
            return statement((PreparedStatement) result, (String) args[0], millis, trace, freezeClock);
          return result;'''
    assert trace.count(anchor) == 1
    return runtime, trace.replace(anchor, replacement)


def analyze(folder):
    read = lambda name: json.loads((folder / name).read_text())
    runtime, trace = read('runtime.json'), read('jdbc-trace.json')
    assert len(runtime) == len(trace) == 13
    duration = sum(row['duration_ns'] for row in runtime) / 1e6
    phase = dict(execute_ms=0.0, commit_ms=0.0, rollback_ms=0.0)
    counts = collections.Counter()
    by_type = collections.defaultdict(lambda: dict(commands=0, total_ms=0.0, execute_ms=0.0, commit_ms=0.0, rollback_ms=0.0))
    for command, events in zip(runtime, trace):
        assert command['id'] == events['id']
        stats = by_type[command['type']]; stats['commands'] += 1; stats['total_ms'] += command['duration_ns'] / 1e6
        for event in events['events']:
            op = event['op']
            key = 'execute_ms' if op.startswith('execute') else (op + '_ms' if op in ('commit','rollback') else None)
            if key:
                milliseconds = event['duration_ns'] / 1e6
                phase[key] += milliseconds; stats[key] += milliseconds; counts[op] += 1
    assert counts['commit'] == 10 and counts['rollback'] == 3
    residual = duration - sum(phase.values()); assert residual >= 0
    statements = read('statements.json')
    business = [row for row in statements if not row['query'].lower().startswith("select set_config")]
    assert business and not any('pg_stat_' in row['query'].lower() for row in business)
    before, after = read('server-before.json'), read('server-after.json')
    wal = {key: float(after['wal'][0][key]) - float(before['wal'][0][key]) for key in ('wal_records','wal_fpi','wal_bytes','wal_buffers_full')}
    assert before['wal'][0]['stats_reset'] == after['wal'][0]['stats_reset']
    io_before = {(r['backend_type'],r['object'],r['context']):r for r in before['io']}
    wal_io = []
    for row in after['io']:
        key = (row['backend_type'],row['object'],row['context'])
        if row['object'] != 'wal': continue
        previous = io_before[key]; assert row['stats_reset'] == previous['stats_reset']
        delta = {field: float(row[field] or 0)-float(previous[field] or 0) for field in ('reads','read_time','writes','write_time','fsyncs','fsync_time')}
        assert all(value >= 0 for value in delta.values())
        wal_io.append(dict(backend_type=key[0],context=key[2],**delta))
    waits = collections.Counter(); blocked = 0; ticks = read('wait-samples.json')
    for tick in ticks:
        for session in tick['sessions']:
            waits[(session['state'],session['wait_event_type'],session['wait_event'])] += 1
            blocked += session['blockers'] > 0
    name = folder.name
    db_before = next(row for row in before['database'] if row['datname'] == name)
    db_after = next(row for row in after['database'] if row['datname'] == name)
    assert read('sampler-errors.json') == []
    return dict(business_duration_ms=duration, phases={**phase,'residual_ms':residual}, calls=dict(counts), by_type=dict(by_type),
                statement_exec_ms=sum(float(row['total_exec_time']) for row in business),
                statement_plan_ms=sum(float(row['total_plan_time']) for row in business),
                statement_wal_bytes=sum(float(row['wal_bytes']) for row in business),
                top_statements=[{k:row[k] for k in ('query','calls','total_plan_time','total_exec_time','shared_blks_hit','shared_blks_read','wal_bytes')} for row in sorted(business,key=lambda r:r['total_exec_time'],reverse=True)[:8]],
                replay_interval_wal=wal,replay_interval_wal_io=wal_io,
                wait_samples=[dict(state=k[0],wait_type=k[1],wait=k[2],count=v) for k,v in waits.items()],
                sampler_ticks=len(ticks),sampler_query_ms=sum(tick['query_ns'] for tick in ticks)/1e6,
                blocked_samples=blocked,deadlocks=db_after['deadlocks']-db_before['deadlocks'],
                scope='Instrumented serial replay; client phases partition business duration; server SQL and WAL timings overlap these phases and must not be added to them; interval WAL includes observation/background activity')


def main():
    out = ROOT/'evidence/kernel-cost'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name = 'reversedb-kernel-'+out.name
    report = dict(status='running',commands=[],inputs={},retained_assets={},cleanup_errors=[])
    created = []; env = dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='1')
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
    def sql(database,query,log):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log).strip()
    def state(database,phase):
        result={}
        for table in TABLES:
            query=f"""SELECT json_build_object('rows',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) rows"""
            result[table]=json.loads(sql(database,query,f'{database}/{phase}-{table}.log'))
        save(out/database/(phase+'-state.json'),result)
        return result
    try:
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        manifest=json.loads((ARCHIVE/'manifest.json').read_text()); om=json.loads((obs/'manifest.json').read_text())
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text()); bundle=ARCHIVE/'bundle'
        src=obs/'run/inputs/app/tpcc/src/reversedb/tpcc'
        source_paths=[src/'AppRuntime.java',src/'JdbcTrace.java']
        for path in source_paths: assert digest(path)==om[str(path.relative_to(obs))]
        scenario=ARCHIVE/'run/inputs/app/tpcc/scenarios/holdout.json'
        invariant=ARCHIVE/'run/inputs/prototype/tpcc/invariants.sql'
        archived=[bundle/'reference.jar',scenario,invariant,*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]
        dependencies=[*sorted(LIB.glob('*.jar')),ROOT/'work/tpcc/runs/c615d3357989/initial.dump']
        for path in archived: assert digest(path)==manifest[str(path.relative_to(ARCHIVE))]
        for path in dependencies: assert digest(path)==retained[str(path)]
        for path in archived+dependencies: report['retained_assets'][str(path)]=digest(path)
        golden=ROOT/'evidence/module-name-pruning/20261105-b14245195231'; gm=json.loads((golden/'manifest.json').read_text())
        expected={}
        for filename in ('responses.json','observations.json','initial-state.json','final-state.json','invariants.json','jdbc-trace.json'):
            path=golden/'holdout_original'/filename; assert digest(path)==gm[str(path.relative_to(golden))]
            expected[filename]=json.loads(path.read_text()); report['retained_assets'][str(path)]=digest(path)
        harness=ROOT/'prototype/module_pruning/KernelCostProbe.java'; lock=ROOT/'work/paperless/images.lock.json'
        for path in [Path(__file__).resolve(),ROOT/'scripts/app_db_smoke.py',ROOT/'scripts/test_name_lookup_pruning.py',harness,lock,scenario,invariant,*source_paths,ARCHIVE/'manifest.json',ARCHIVE/'retained-assets.json',obs/'manifest.json',golden/'manifest.json']:
            freeze(path)
        protocol=dict(runs=['warmup','trial1','trial2','trial3'],measured=['trial1','trial2','trial3'],commands_per_run=17,
            workload='Frozen holdout: all five transaction types, ten commits, three business aborts, four input rejections',
            storage='New Docker named volume on the Docker Desktop VM disk; not tmpfs and not proof of physical host durability',
            guarantees='fsync=on, synchronous_commit=on, full_page_writes=on, wal_level=replica; original SERIALIZABLE/rollback and all data retained',
            instrumentation='Same JVM for warmup and measured runs; separate identical restored databases; business-only pg_stat_statements top-level tracking; client execute/commit/rollback timing; 10 ms requested wait sampling',
            limits=['Three diagnostic repeats, not a throughput benchmark','Serial workload cannot measure multiuser contention or certify lock removal','WAL interval counters include observation/background work','Server SQL time includes I/O and internal work; not exclusive CPU time','No kernel stack CPU profile or crash test','Docker volume durability is not host power-loss verification'],
            stop='One fixed collection; no tuning, feature disabling, added workload or automatic deletion')
        save(out/'protocol.json',protocol); report['protocol']=protocol
        generated=out/'generated'; generated.mkdir(); classes=out/'classes'; classes.mkdir(); empty=out/'empty-sourcepath'; empty.mkdir()
        texts=instrument(*(p.read_text() for p in source_paths))
        for path,text in zip(source_paths,texts):
            (generated/path.name).write_text(text)
            (out/(path.stem+'.diff')).write_text(''.join(difflib.unified_diff(path.read_text().splitlines(True),text.splitlines(True),fromfile='frozen/'+path.name,tofile='instrumented/'+path.name)))
        libs=':'.join(map(str,sorted(LIB.glob('*.jar'))))
        cp=str(bundle/'candidate-classes')+':'+libs
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,*sorted(generated.glob('*.java')),harness],'compile.log')
        image=json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']
        report['image']=image
        run(DOCKER+['volume','create','--label','reversedb.experiment=kernel-cost',name],'create-volume.log'); created.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=kernel-cost',
            '--memory=1536m','--cpus=2','--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432',
            '-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on','-c','wal_level=replica',
            '-c','shared_preload_libraries=pg_stat_statements','-c','pg_stat_statements.track=none','-c','pg_stat_statements.track_planning=on',
            '-c','pg_stat_statements.track_utility=off','-c','track_io_timing=on','-c','track_wal_io_timing=on'],'start.log'); created.append('container')
        import time
        for attempt in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{attempt}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        mounts=json.loads(run(DOCKER+['inspect',name,'--format','{{json .Mounts}}'],'mounts.json'))
        assert any(m['Type']=='volume' and m['Name']==name and m['Destination']=='/var/lib/postgresql' for m in mounts)
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log').strip(); assert port.startswith('127.0.0.1:') and '\n' not in port
        sql('tpcc','CREATE EXTENSION pg_stat_statements','extension.log')
        settings=json.loads(sql('tpcc',"SELECT json_agg(x) FROM (SELECT name,setting,source FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes','wal_level','track_io_timing','track_wal_io_timing','pg_stat_statements.track','pg_stat_statements.track_planning','pg_stat_statements.track_utility','autovacuum','data_directory','server_version','wal_sync_method') ORDER BY name) x",'settings.log'))
        save(out/'settings.json',settings); values={s['name']:s['setting'] for s in settings}
        for setting in ('fsync','synchronous_commit','full_page_writes','track_io_timing','track_wal_io_timing','pg_stat_statements.track_planning','autovacuum'): assert values[setting]=='on'
        assert values['wal_level']=='replica' and values['pg_stat_statements.track']=='none' and values['pg_stat_statements.track_utility']=='off'
        run(DOCKER+['exec',name,'df','-T',values['data_directory']],'filesystem.log')
        plans=[]
        for trial in protocol['runs']:
            (out/trial).mkdir(); sql('tpcc','CREATE DATABASE '+trial,trial+'/create.log')
            with dependencies[-1].open('rb') as stream:
                run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d',trial],trial+'/restore.log',stdin=stream)
            sql(trial,'ANALYZE',trial+'/analyze.log')
            assert state(trial,'initial')==expected['initial-state.json']
            plans.append(dict(name=trial,database=trial,output=str(out/trial)))
            print('Prepared '+trial,flush=True)
        save(out/'plan.json',dict(commands=str(scenario),runs=plans))
        runtime_cp=str(classes)+':'+str(bundle/'reference-adapter')+':'+str(bundle/'reference.jar')+':'+cp
        run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2',f'-Xlog:class+load=info:file={out/"class-load.log"}',
             '-cp',runtime_cp,'research.KernelCostProbe',f'jdbc:postgresql://{port}/',out/'plan.json'],'probe.log',timeout=240)
        loads=(out/'class-load.log').read_text()
        for class_name,origin in [('reversedb.tpcc.AppRuntime',classes),('reversedb.tpcc.JdbcTrace',classes),('com.oltpbenchmark.api.TpccApp',bundle/'reference-adapter')]:
            lines=[line for line in loads.splitlines() if ' '+class_name+' source:' in line]
            assert len(lines)==1 and str(origin) in lines[0],class_name
        summaries={}
        for trial in protocol['runs']:
            assert state(trial,'final')==expected['final-state.json']
            values=json.loads(sql(trial,invariant.read_text(),trial+'/invariants.log')); save(out/trial/'invariants.json',values)
            assert values==expected['invariants.json']
            for filename in ('responses.json','observations.json'):
                assert json.loads((out/trial/filename).read_text())==expected[filename],(trial,filename)
            trace=json.loads((out/trial/'jdbc-trace.json').read_text())
            clean=[{**row,'events':[{k:v for k,v in event.items() if k not in ('duration_ns','outcome')} for event in row['events']]} for row in trace]
            assert clean==expected['jdbc-trace.json'],trial
            summaries[trial]=analyze(out/trial)
        measured=[summaries[name] for name in protocol['measured']]
        summary=dict(runs=summaries,measured_trials=protocol['measured'],
            median_business_ms=statistics.median(r['business_duration_ms'] for r in measured),
            median_phase_ms={key:statistics.median(r['phases'][key] for r in measured) for key in measured[0]['phases']},
            note='Medians of separate phases need not sum to median total. No addition of overlapping server/IO/client timers.')
        save(out/'summary.json',summary)
        for path,sha in report['retained_assets'].items(): assert digest(Path(path))==sha
        report.update(status='passed',total_replayed_commands=68,measured_commands=51,measured_accepted_transactions=39,
                      semantic_validation='All four runs match frozen responses, step observations, full nine-table states and normalized JDBC trace',summary_file='summary.json')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(created):
            try:
                if resource=='container':
                    run(DOCKER+['logs',name],'postgres.log',check=False)
                    run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report)
        save(out/'manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete collection')


if __name__=='__main__': main()
