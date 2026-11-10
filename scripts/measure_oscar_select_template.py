#!/usr/bin/env python3
"""Predeclared paired latency protocol; never tune or repeat based on the result."""
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import time
import uuid
from placement_frontend import select_complete_contract

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/oscar-baseline/20261106-ed9f704d86d6'
VALIDATED=ROOT/'evidence/oscar-select-template/20261107-5681be16f8c7'
CONTRACT=ROOT/'evidence/oscar-allocation-contract/20261107-979627b9d9bb'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE=ROOT/'work/oscar/source-a5ed3c9c'
PYTHON=ROOT/'work/oscar/venv/bin/python'
DOCKER=['docker','--context','desktop-linux']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out=ROOT/'evidence/oscar-select-template-performance'/('20261107-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-oscar-template-perf-'+out.name;resources=[]
    report=dict(status='running',checks=[],commands=[],cleanup_errors=[],retained_assets={})
    def save(n,o):(out/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
    def check(n,ok):
        assert ok,n
        report['checks'].append(n)
    def load(p):return json.loads(p.read_text())
    def run(command,log,stdin=None,required=True,env=None):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q,log,db='postgres',user='postgres'):
        return run(DOCKER+['exec',name,'psql','-U',user,'-d',db,'-XAt','-v','ON_ERROR_STOP=1','-c',q],log)
    try:
        def retain(base,f):
            p=base/f;assert sha(p)==load(base/'manifest.json')[f],f
            report['retained_assets'][str(p)]=sha(p);return p
        for f in ['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/after-full-state.json','a/loaded-sources.json']:
            retain(BASE,f)
        check('fixed SELECT template differential passed',load(retain(VALIDATED,'run.json'))['status']=='passed')
        for f in ['select_template.py','baseline_probe.py']:
            check('exact validated '+f,sha(ROOT/'prototype/oscar'/f)==sha(retain(VALIDATED,'inputs/prototype/oscar/'+f)))
        for f,h in load(BASE/'source-manifest.json').items():assert sha(SOURCE/f)==h,f
        for n,item in load(BASE/'a/loaded-sources.json').items():assert sha(Path(item['path']))==item['sha256'],n
        check('application/framework/driver unchanged',True)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('original core unchanged '+f,sha(ROOT/'scripts'/f)==sha(retain(FIRST,'inputs/scripts/'+f)))
        for case in ['neworder','payment']:
            check(case+' original selector result unchanged',select_complete_contract(load(retain(FIRST,case+'-contract.json')))['selection']==load(retain(FIRST,case+'-selection.json')))
        check('Oscar formal selection still retained',select_complete_contract(load(retain(CONTRACT,'sql-boundary-contract.json')))==load(retain(CONTRACT,'sql-boundary-selection.json')))
        for p in [Path(__file__).resolve(),ROOT/'prototype/oscar/select_template_performance_probe.py',ROOT/'prototype/oscar/select_template.py',ROOT/'prototype/oscar/baseline_probe.py']:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        schedule=[]
        for pair in range(10):
            arms=['original','candidate'] if pair%2==0 else ['candidate','original']
            for arm in arms:schedule.append(dict(id=f'p{pair:02d}-{arm}',pair=pair,arm=arm,measured=pair>=2,database=f'oscar_p{pair:02d}_{arm}'))
        protocol=dict(warmup_pairs=2,measurement_pairs=8,orders_per_trial=4,schedule=schedule,
            primary='sum of four order-placement latencies including original outer transaction and real commit',
            secondary='preparation-plus-placement wall time and current application-thread CPU during placement; no server CPU claim',
            cache='per-trial empty template cache; six cold builds included; 40 hits within four orders; validation/preparation never populate candidate cache',
            reporting='all eight paired values, median-of-arm totals, median paired speedup, wins, min/max and exploratory paired-bootstrap interval',
            bootstrap=dict(samples=20000,seed=20261107,statistic='median of paired original/candidate ratios',interval='percentile 95%; descriptive small sample, not arbitrary-run or production guarantee'),
            stop='execute this schedule once; retain all samples; no outlier removal, retuning or additional timing based on outcome',
            scope='sequential fixed fixture OrderCreator benchmark, not HTTP checkout, concurrent throughput or admitted production replacement')
        save('protocol.json',protocol)
        save('host.json',dict(platform=platform.platform(),machine=platform.machine(),cpu_count=os.cpu_count(),load_average=os.getloadavg()))
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres'];report['image']=image
        check('pinned local image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-select-template-performance',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-select-template-performance','--memory=1024m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-performance-admin',
            image['resolved'],'-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-performance-only'",'role.log')
        sql('CREATE DATABASE oscar_fixture OWNER oscar_user','fixture-create.log')
        with (BASE/'fixture.sql').open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','oscar_user','-d','oscar_fixture','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        for item in schedule:sql('CREATE DATABASE '+item['database']+' WITH TEMPLATE oscar_fixture OWNER oscar_user',item['id']+'-create.log')
        sql("SELECT name,setting FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes','shared_buffers','jit','max_connections','server_version','default_transaction_isolation') ORDER BY name",'postgres-settings.log')
        host=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',TZ='UTC',PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
            DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_PORT=host.split(':')[1],
            DATABASE_NAME='oscar_fixture',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-performance-only',
            RDB_PERFORMANCE_PROTOCOL=str(out/'protocol.json'),RDB_REQUESTS=str(BASE/'fixture-request.json'),
            RDB_FIXTURE_STATE=str(BASE/'fixture-full-state.json'),RDB_EXPECTED_STATE=str(BASE/'a/after-full-state.json'))
        print('Running predeclared 2 warmup + 8 measured pairs',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/select_template_performance_probe.py','measure',out/'measurement'],'measurement.log',env=env)
        result=load(out/'measurement/result.json');check('exact schedule completed',result['status']=='passed' and len(result['runs'])==20)
        measured=[r for r in result['runs'] if r['measured']];check('eight measured pairs only',len(measured)==16)
        reference=result['runs'][0]['objects']
        check('all 20 full order results identical',all(r['objects']==reference for r in result['runs']))
        expected_objects=[o['order'] for o in load(retain(BASE,'a/result.json'))['orders']]
        check('all returned order fields equal frozen original',reference==expected_objects)
        pairs=[]
        for pair in range(2,10):
            a=next(r for r in measured if r['pair']==pair and r['arm']=='original');b=next(r for r in measured if r['pair']==pair and r['arm']=='candidate')
            pairs.append(dict(pair=pair,first_arm='original' if pair%2==0 else 'candidate',original_ms=a['placement_ns']/1e6,candidate_ms=b['placement_ns']/1e6,
                speedup=a['placement_ns']/b['placement_ns'],original_thread_cpu_ms=a['thread_cpu_ns']/1e6,candidate_thread_cpu_ms=b['thread_cpu_ns']/1e6,original_prepare_and_place_ms=a['prepare_and_place_ns']/1e6,candidate_prepare_and_place_ms=b['prepare_and_place_ns']/1e6))
        ratios=[p['speedup'] for p in pairs];rng=random.Random(20261107)
        bootstrap=sorted(statistics.median(rng.choices(ratios,k=8)) for _ in range(20000))
        a=statistics.median(p['original_ms'] for p in pairs);b=statistics.median(p['candidate_ms'] for p in pairs)
        summary=dict(pairs=pairs,original_median_ms=a,candidate_median_ms=b,ratio_of_medians=a/b,latency_reduction_percent=100*(a-b)/a,
            median_paired_speedup=statistics.median(ratios),exploratory_bootstrap_95=[bootstrap[499],bootstrap[19499]],candidate_faster_pairs=sum(r>1 for r in ratios),
            original_range_ms=[min(p['original_ms'] for p in pairs),max(p['original_ms'] for p in pairs)],
            candidate_range_ms=[min(p['candidate_ms'] for p in pairs),max(p['candidate_ms'] for p in pairs)],
            secondary_original_median_ms=statistics.median(p['original_prepare_and_place_ms'] for p in pairs),
            secondary_candidate_median_ms=statistics.median(p['candidate_prepare_and_place_ms'] for p in pairs),
            original_thread_cpu_median_ms=statistics.median(p['original_thread_cpu_ms'] for p in pairs),
            candidate_thread_cpu_median_ms=statistics.median(p['candidate_thread_cpu_ms'] for p in pairs),
            execute_per_trial=dict(original=187,candidate=187),orders_executed=80,measured_orders=64,warmup_orders=16,
            limits=['all samples retained; exploratory interval from 8 sequential pairs, not a production significance guarantee',
                    'fresh database/backend per trial; first-use planning and sandbox DEBUG SQL logging included',
                    'not HTTP end-to-end latency or concurrent throughput',
                    'frozen SELECT template adapter only; original allocation path retained; no combined optimization',
                    'per-trial cold cache and guards included; current-thread CPU excludes server and other threads',
                    'not production admission or a new region selected by the unchanged DB-call DP'])
        save('summary.json',summary)
        check('all original core/runtime evidence preserved',all(sha(Path(p))==h for p,h in report['retained_assets'].items()))
        report.update(status='passed',runtime_checks=len(result['checks']),measurement_pairs=8,warmup_pairs=2)
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
