#!/usr/bin/env python3
"""Fixed original/candidate differential experiment for the Python result ABI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from placement_frontend import select_complete_contract

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/oscar-baseline/20261106-ed9f704d86d6'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE=ROOT/'work/oscar/source-a5ed3c9c'
PYTHON=ROOT/'work/oscar/venv/bin/python'
DOCKER=['docker','--context','desktop-linux']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out=ROOT/'evidence/oscar-handoff'/('20261107-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-oscar-handoff-'+out.name;resources=[]
    report=dict(status='running',checks=[],commands=[],cleanup_errors=[],retained_assets={},performance_claim=None)
    def save(n,o):(out/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
    def check(n,ok):
        assert ok,n
        report['checks'].append(n)
    def run(command,log,stdin=None,required=True,env=None):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q,log):return run(DOCKER+['exec',name,'psql','-U','postgres','-d','postgres','-XAt','-v','ON_ERROR_STOP=1','-c',q],log)
    def load(path):return json.loads(path.read_text())
    try:
        manifests={BASE:load(BASE/'manifest.json'),FIRST:load(FIRST/'manifest.json')}
        def retain(base,f):
            p=base/f;assert sha(p)==manifests[base][f],f
            report['retained_assets'][str(p)]=sha(p);return p
        for f in ['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/loaded-sources.json','a/executed-code.json','a/RDB-ONE-trace.json','a/after-full-state.json']:
            retain(BASE,f)
        for f in ['fixture-request.json','fixture-full-state.json']:shutil.copy2(BASE/f,out/f)
        for f,h in load(BASE/'source-manifest.json').items():assert sha(SOURCE/f)==h,f
        for n,info in load(BASE/'a/loaded-sources.json').items():assert sha(Path(info['path']))==info['sha256'],n
        check('pinned upstream/framework/driver sources unchanged',True)
        files=[Path(__file__).resolve(),ROOT/'scripts/placement_frontend.py']+[ROOT/'prototype/oscar'/f for f in ['baseline_probe.py','allocation_handoff.py','handoff_probe.py']]
        files += [ROOT/'scripts'/f for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        for p in files:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged first-project core '+f,sha(ROOT/'scripts'/f)==sha(retain(FIRST,'inputs/scripts/'+f)))
        for case in ['neworder','payment']:
            check(case+' selection unchanged',select_complete_contract(load(retain(FIRST,case+'-contract.json')))['selection']==load(retain(FIRST,case+'-selection.json')))
        codes=load(BASE/'a/executed-code.json');events=load(BASE/'a/RDB-ONE-trace.json');binding={}
        for label,prefix in [('update','UPDATE "partner_stockrecord"'),('read','SELECT "partner_stockrecord"."id", "partner_stockrecord"."num_allocated"')]:
            event=next(e for e in events if e['kind']=='execute' and e['sql'].startswith(prefix))
            frame=next(f for f in event['stack'] if codes[f['code']]['name']=='AbstractStockRecord.allocate')
            binding[label+'_sql']=event['sql'];binding[label+'_offset']=frame['offset'];binding['allocate_code_hash']=frame['code']
        save('binding.json',binding)
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres'];report['image']=image
        check('pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-handoff',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-handoff',
            '--memory=1024m','--cpus=2','--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432',
            '-e','POSTGRES_PASSWORD=synthetic-handoff-admin',image['resolved'],'-c','timezone=UTC','-c','shared_buffers=128MB',
            '-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-handoff-only'",'role.log')
        sql('CREATE DATABASE oscar_fixture OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','oscar_user','-d','oscar_fixture','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',TZ='UTC',PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
            DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_PORT=host.split(':')[1],
            DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-handoff-only',RDB_HANDOFF_BINDING=str(out/'binding.json'))
        for scope in ['local','replay']:
            for arm in ['original','candidate']:
                db='oscar_'+scope+'_'+arm
                sql('CREATE DATABASE '+db+' WITH TEMPLATE oscar_fixture OWNER oscar_user',db+'-create.log')
                print('Running '+scope+' '+arm,flush=True)
                run([PYTHON,ROOT/'prototype/oscar/handoff_probe.py',scope,out/(scope+'-'+arm)],scope+'-'+arm+'.log',
                    env=dict(env,DATABASE_NAME=db,RDB_HANDOFF_ARM=arm))
        local_a=load(out/'local-original/local-result.json');local_b=load(out/'local-candidate/local-result.json')
        check('fixed eleven actual-host cases executed in both arms',len(local_a['cases'])==len(local_b['cases'])==11)
        deltas=[];diagnostic_differences=[]
        for a,b in zip(local_a['cases'],local_b['cases']):
            def comparable(x):
                result={k:v for k,v in x.items() if k not in ['physical_execute_count','stats']}
                if result['error']:result['error']={k:v for k,v in result['error'].items() if k!='full_text'}
                return result
            check(a['case']+' model events/fields and error class/primary/code match',comparable(a)==comparable(b))
            if a['error'] and a['error']['full_text']!=b['error']['full_text']:
                diagnostic_differences.append(dict(case=a['case'],original=a['error']['full_text'],candidate=b['error']['full_text']))
            file=a['case']+'-full-state.json'
            check(a['case']+' all tables and sequences match',load(out/'local-original'/file)==load(out/'local-candidate'/file))
            deltas.append(dict(case=a['case'],original=a['physical_execute_count'],candidate=b['physical_execute_count'],stats=b['stats']))
        check('local experiments preserve complete fixture',load(out/'local-original/after-full-state.json')==load(out/'fixture-full-state.json')==load(out/'local-candidate/after-full-state.json'))
        a=load(out/'replay-original/result.json');b=load(out/'replay-candidate/result.json')
        compare_order=lambda x:{k:v for k,v in x.items() if k not in ['execute_count','sql_verbs']}
        check('all four order objects and identity observations match',[compare_order(o) for o in a['orders']]==[compare_order(o) for o in b['orders']])
        check('whole replay full database matches',load(out/'replay-original/after-full-state.json')==load(out/'replay-candidate/after-full-state.json')==load(BASE/'a/after-full-state.json'))
        expected=[1,3,6,0]
        check('one actual DB-API execute removed per allocation',[x['execute_count']-y['execute_count'] for x,y in zip(a['orders'],b['orders'])]==expected)
        # Compare all observed model events, excluding source-stack representation.
        for n in ['RDB-ONE','RDB-THREE','RDB-SIX','RDB-DIGITAL']:
            events_a=load(out/'replay-original'/(n+'-trace.json'));events_b=load(out/'replay-candidate'/(n+'-trace.json'))
            check(n+' original model call/return and identity events match',[e for e in events_a if e['kind'].startswith('python_')]==[e for e in events_b if e['kind'].startswith('python_')])
        check('server error context mismatch recorded rather than hidden',[d['case'] for d in diagnostic_differences]==['overflow'])
        save('comparison.json',dict(local=deltas,diagnostic_differences=diagnostic_differences,orders=[dict(number=x['number'],original_calls=x['execute_count'],candidate_calls=y['execute_count']) for x,y in zip(a['orders'],b['orders'])],
             original_local_checks=len(local_a['checks']),candidate_local_checks=len(local_b['checks']),order_checks_per_arm=len(a['checks'])))
        save('decision.json',dict(status='bounded runtime prototype verified, not production/general backend admission',
            mechanism='source-bound compiler boundary handoff: invoker ordered UPDATE/SELECT packet, original result conversion/from_db/get/refresh and callbacks retained',
            algorithm='first-project optimizer unchanged; backend adapter hand-written, no automatic synthesis claim',
            next='audit ABI/schema/role gates and remaining concurrency before an admitted shared capability or performance experiment',
            unknowns=['custom managers/routers/cursors and debug instrumentation outside frozen configuration',
                      'concurrent missing-row insertion and multi-thread candidate execution not tested',
                      'overflow full error text differs by server function CONTEXT; retained mismatch prevents full-observable equivalence admission',
                      'function provisioning/EXECUTE permission is an extra deployment requirement'],
            performance_claim=None,complete_data=True,default_application_modified=False))
        report.update(status='passed',local_cases=11,order_cases=4,original_local_checks=len(local_a['checks']),candidate_local_checks=len(local_b['checks']),order_checks_per_arm=len(a['checks']))
        for p,h in report['retained_assets'].items():assert sha(Path(p))==h,p
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
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
