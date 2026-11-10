#!/usr/bin/env python3
"""Bounded source/bytecode-bound module cost attribution, no optimizer changes."""
import ast
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import statistics
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/oscar-baseline/20261106-ed9f704d86d6'
VALIDATED=ROOT/'evidence/oscar-handoff/20261107-e1d257949c8b'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE=ROOT/'work/oscar/source-a5ed3c9c'
PYTHON=ROOT/'work/oscar/venv/bin/python'
DOCKER=['docker','--context','desktop-linux']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())


def analyze(folder):
    data=load(folder/'result.json');codes=load(folder/'executed-code.json')
    summary=dict(trials=[],arms={})
    for r in data['runs']:
        o=r['orders']
        summary['trials'].append(dict(id=r['id'],arm=r['arm'],profiled=r['profiled'],warmup=r['warmup'],
            wall_ms=sum(x['wall_ns'] for x in o)/1e6,cpu_ms=sum(x['cpu_ns'] for x in o)/1e6,
            driver_execute_wall_ms=sum(x['execute_wall_ns'] for x in o)/1e6,
            driver_execute_cpu_ms=sum(x['execute_cpu_ns'] for x in o)/1e6,
            commit_wall_ms=sum(x['commit_wall_ns'] for x in o)/1e6,
            server_top_plan_ms=sum(x['server_top_plan_ms'] for x in o),server_top_exec_ms=sum(x['server_top_exec_ms'] for x in o)))
    for arm in ['original','candidate']:
        trials=[x for x in summary['trials'] if x['arm']==arm and not x['warmup']]
        profiled=[x for x in trials if x['profiled']];controls=[x for x in trials if not x['profiled']]
        profs=[load(folder/r['id']/'cpu-profile.json') for r in profiled]
        modules=defaultdict(lambda:dict(self_cpu_ms=0,calls=0))
        funcs=defaultdict(lambda:dict(self_cpu_ms=0,inclusive_cpu_ms=0,calls=0))
        for p in profs:
            for f in p['functions']:
                mod=modules[f['module']];mod['self_cpu_ms']+=f['self_cpu_ms']/len(profs);mod['calls']+=f['calls']/len(profs)
                key=(f['module'],f['name'],f.get('line',0))
                target=funcs[key]
                for field in ['self_cpu_ms','inclusive_cpu_ms','calls']:target[field]+=f[field]/len(profs)
                target['source']=f.get('file');target['code_sha256']=f.get('code_sha256')
        phase=defaultdict(lambda:dict(execute_calls=0,execute_wall_ms=0,execute_cpu_ms=0))
        sql_owners=defaultdict(lambda:dict(calls=0,wall_ms=0,cpu_ms=0))
        direct_allocate=defaultdict(lambda:dict(calls=0,wall_ms=0,cpu_ms=0))
        for r in profiled:
            for e in load(folder/r['id']/'events.json'):
                if e['kind']!='execute':continue
                app=[f for f in reversed(e['stack']) if f['module'].startswith('oscar.')]
                names=[codes[f['code']]['name'] for f in app]
                region=next((n for n in names if n!='OrderCreator.place_order'),
                            'OrderCreator.place_order (direct)' if names else 'outside-application')
                owner=names[-1] if names else 'outside-application'
                d=phase[region];d['execute_calls']+=1/len(profiled);d['execute_wall_ms']+=e['wall_ns']/1e6/len(profiled);d['execute_cpu_ms']+=e['cpu_ns']/1e6/len(profiled)
                d=sql_owners[owner];d['calls']+=1/len(profiled);d['wall_ms']+=e['wall_ns']/1e6/len(profiled);d['cpu_ms']+=e['cpu_ns']/1e6/len(profiled)
                alloc=next((f for f in e['stack'] if codes[f['code']]['name']=='AbstractStockRecord.allocate'),None)
                if alloc and alloc['offset'] in [228,266]:
                    key='update' if alloc['offset']==228 else 'refresh'
                    d=direct_allocate[key];d['calls']+=1/len(profiled);d['wall_ms']+=e['wall_ns']/1e6/len(profiled);d['cpu_ms']+=e['cpu_ns']/1e6/len(profiled)
        med=lambda rows,key:statistics.median(x[key] for x in rows)
        arm_result=dict(control_wall_ms=med(controls,'wall_ms'),profile_wall_ms=med(profiled,'wall_ms'),
            control_cpu_ms=med(controls,'cpu_ms'),profile_cpu_ms=med(profiled,'cpu_ms'),
            observation_wall_factor=med(profiled,'wall_ms')/med(controls,'wall_ms'),
            observation_cpu_factor=med(profiled,'cpu_ms')/med(controls,'cpu_ms'),
            control_driver_execute_wall_ms=med(controls,'driver_execute_wall_ms'),
            control_driver_execute_cpu_ms=med(controls,'driver_execute_cpu_ms'),
            control_commit_wall_ms=med(controls,'commit_wall_ms'),
            control_server_top_plan_ms=med(controls,'server_top_plan_ms'),control_server_top_exec_ms=med(controls,'server_top_exec_ms'),
            module_cpu=[dict(module=k,**v) for k,v in sorted(modules.items(),key=lambda kv:-kv[1]['self_cpu_ms'])],
            function_cpu=[dict(module=k[0],name=k[1],line=k[2],**v) for k,v in sorted(funcs.items(),key=lambda kv:-kv[1]['self_cpu_ms'])],
            source_phase_db=[dict(phase=k,**v) for k,v in sorted(phase.items(),key=lambda kv:-kv[1]['execute_wall_ms'])],
            nearest_app_owner_db=[dict(owner=k,**v) for k,v in sorted(sql_owners.items(),key=lambda kv:-kv[1]['wall_ms'])],
            previously_selected_update_refresh=dict(direct_allocate))
        arm_result['control_non_driver_wall_ms']=arm_result['control_wall_ms']-arm_result['control_driver_execute_wall_ms']-arm_result['control_commit_wall_ms']
        summary['arms'][arm]=arm_result
    summary['limits']=['CPU rankings are deterministic-profile observations, not unperturbed absolute costs',
        'exclusive module CPU is additive; inclusive function/call-edge CPU overlaps',
        'server SQL plan/execute times are wall times, not server CPU; do not add to driver wall',
        'driver wall minus current-thread CPU includes server/network/scheduling; no pure network-wait attribution',
        'dynamic call edges describe observed workload only and do not authorize pruning unobserved modules',
        'native CPU, freezegun, DEBUG logging and probe overhead are visible and must not be mistaken for removable business modules']
    return summary


def main():
    out=ROOT/'evidence/oscar-module-profile'/('20261107-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-oscar-profile-'+out.name;resources=[]
    report=dict(status='running',checks=[],commands=[],cleanup_errors=[],retained_assets={})
    def save(n,o):(out/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
    def check(n,ok):
        assert ok,n
        report['checks'].append(n)
    def run(command,log,stdin=None,required=True,env=None):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q,log,db='postgres',user='postgres'):return run(DOCKER+['exec',name,'psql','-U',user,'-d',db,'-XAt','-v','ON_ERROR_STOP=1','-c',q],log)
    try:
        def retain(base,f):
            p=base/f;assert sha(p)==load(base/'manifest.json')[f],f
            report['retained_assets'][str(p)]=sha(p);return p
        for f in ['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/after-full-state.json','a/loaded-sources.json']:retain(BASE,f)
        binding=retain(VALIDATED,'binding.json')
        for f in ['allocation_handoff.py','baseline_probe.py']:
            check('validated '+f+' unchanged',sha(ROOT/'prototype/oscar'/f)==sha(retain(VALIDATED,'inputs/prototype/oscar/'+f)))
        for f,h in load(BASE/'source-manifest.json').items():assert sha(SOURCE/f)==h,f
        for n,item in load(BASE/'a/loaded-sources.json').items():assert sha(Path(item['path']))==item['sha256'],n
        check('pinned original source/framework/driver unchanged',True)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('original optimizer unchanged '+f,sha(ROOT/'scripts'/f)==sha(retain(FIRST,'inputs/scripts/'+f)))
        for p in [Path(__file__).resolve(),ROOT/'prototype/oscar/module_profile_probe.py',ROOT/'prototype/oscar/allocation_handoff.py',ROOT/'prototype/oscar/baseline_probe.py']:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        compiled={}
        for p in sorted((SOURCE/'src/oscar').rglob('*.py')):
            pyc=Path(py_compile.compile(str(p),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH))
            compiled[str(p.relative_to(SOURCE))]=dict(source_sha256=sha(p),pyc_sha256=sha(pyc))
        save('controlled-pyc-inputs.json',compiled)
        # Two warmups, then two reversed blocks of matched control/profile trials.
        schedule=[dict(id='warmup-original',arm='original',profiled=False,warmup=True),dict(id='warmup-candidate',arm='candidate',profiled=False,warmup=True)]
        groups=[('original',False),('original',True),('candidate',False),('candidate',True)]
        for block in range(2):
            for arm,profiled in (groups if block==0 else reversed(groups)):
                schedule.append(dict(id=f'b{block}-{arm}-'+('profile' if profiled else 'control'),arm=arm,profiled=profiled,warmup=False))
        for i,item in enumerate(schedule):item['database']=f'oscar_profile_{i:02d}'
        save('protocol.json',dict(schedule=schedule,orders_per_trial=4,
            clocks='real perf_counter_ns and current-thread CPU; deterministic cProfile uses thread_time_ns',
            database='per-order pg_stat_statements reset by separate admin session; app DB/user filtered; top and nested statements separated',
            controls='same driver wall/CPU timers and server instrumentation, but no cProfile or stack walks',
            stop='fixed two warmups plus two reversed blocks; no new optimizer, deletion, enlarged workload or retiming based on profile rankings'))
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres'];report['image']=image
        check('pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-module-profile',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-module-profile','--memory=1024m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-profile-admin',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on',
            '-c','shared_preload_libraries=pg_stat_statements','-c','pg_stat_statements.track=all','-c','pg_stat_statements.track_planning=on'],
            'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-profile-only'",'role.log')
        sql('CREATE DATABASE oscar_fixture OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','oscar_user','-d','oscar_fixture','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        sql('CREATE EXTENSION pg_stat_statements','extension.log','oscar_fixture')
        tree=ast.parse((ROOT/'prototype/oscar/allocation_handoff.py').read_text())
        fn=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PACKET_SQL' for t in n.targets))
        sql(fn,'function.log','oscar_fixture','oscar_user')
        sql('REVOKE ALL ON FUNCTION public.reversedb_allocate_packet(integer,bigint) FROM PUBLIC','privileges.log','oscar_fixture','oscar_user')
        for item in schedule:sql('CREATE DATABASE '+item['database']+' WITH TEMPLATE oscar_fixture OWNER oscar_user',item['id']+'-create.log')
        host=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',TZ='UTC',PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
            DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_PORT=host.split(':')[1],
            DATABASE_NAME='oscar_fixture',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-profile-only',RDB_PROFILE_ADMIN_PASSWORD='synthetic-profile-admin',
            RDB_PROFILE_PROTOCOL=str(out/'protocol.json'),RDB_HANDOFF_BINDING=str(binding),RDB_REQUESTS=str(BASE/'fixture-request.json'),
            RDB_FIXTURE_STATE=str(BASE/'fixture-full-state.json'),RDB_EXPECTED_STATE=str(BASE/'a/after-full-state.json'))
        print('Running fixed module profiles and observation-overhead controls',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/module_profile_probe.py','profile',out/'profiles'],'profile.log',env=env)
        result=load(out/'profiles/result.json');check('exact ten trials completed',result['status']=='passed' and len(result['runs'])==10)
        summary=analyze(out/'profiles');save('summary.json',summary)
        check('all original source/evidence preserved',all(sha(Path(p))==h for p,h in report['retained_assets'].items()))
        report.update(status='passed',runtime_checks=len(result['checks']),trial_count=10,compiled_oscar_files=len(compiled),optimizer_changed=False)
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container':
                    run(DOCKER+['logs',name],'postgres.log',required=False);run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else:run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error:report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:report['status']='failed'
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),runtime_checks=report.get('runtime_checks'))),flush=True)


if __name__=='__main__':main()
