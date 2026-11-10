#!/usr/bin/env python3
"""Bounded allocation contract checks; retain unknown host boundary in shared DP."""
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
    out=ROOT/'evidence/oscar-allocation-contract'/('20261107-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-oscar-contract-'+out.name
    report=dict(status='running',checks=[],commands=[],cleanup_errors=[],retained_assets={},actual_runtime_replacement=False)
    resources=[]
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
    try:
        manifests={BASE:json.loads((BASE/'manifest.json').read_text()),FIRST:json.loads((FIRST/'manifest.json').read_text())}
        def retain(base,f):
            p=base/f;assert sha(p)==manifests[base][f],f
            report['retained_assets'][str(p)]=sha(p);return p
        for f in ['fixture.sql','source-manifest.json','a/loaded-sources.json','a/bytecode-witness.json','a/executed-code.json','a/RDB-ONE-trace.json']:
            retain(BASE,f)
        for f,h in json.loads((BASE/'source-manifest.json').read_text()).items():assert sha(SOURCE/f)==h,f
        check('all pinned upstream source files unchanged',True)
        for n,info in json.loads((BASE/'a/loaded-sources.json').read_text()).items():assert sha(Path(info['path']))==info['sha256'],n
        check('loaded framework and driver files unchanged from baseline',True)
        probe=ROOT/'prototype/oscar/allocation_contract_probe.py'
        for p in [Path(__file__).resolve(),probe,ROOT/'scripts/placement_frontend.py']+[ROOT/'scripts'/f for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('first-project core unchanged '+f,sha(ROOT/'scripts'/f)==sha(retain(FIRST,'inputs/scripts/'+f)))
        for case in ['neworder','payment']:
            contract=json.loads(retain(FIRST,case+'-contract.json').read_text())
            check(case+' original selection unchanged',select_complete_contract(contract)['selection']==json.loads(retain(FIRST,case+'-selection.json').read_text()))
        # This is the SQL subregion only; it does not price arbitrary signal or model callbacks.
        codes=json.loads((BASE/'a/executed-code.json').read_text())
        trace=json.loads((BASE/'a/RDB-ONE-trace.json').read_text())
        events=[e for e in trace if e['kind']=='execute' and any(codes[f['code']]['name']=='AbstractStockRecord.allocate' for f in e['stack'])]
        u=next(e for e in events if e['sql'].startswith('UPDATE "partner_stockrecord"'))
        r=next(e for e in events if e['sql'].startswith('SELECT "partner_stockrecord"."id", "partner_stockrecord"."num_allocated"'))
        contract=dict(scope='only the source-ordered SQL boundary, not entire allocate or full order',
            external_inputs={'pk':'int64','quantity':'int32'},parameters={},wire_types={},
            nodes=[],edges=[dict(before='update',after='read',reason='original statement order and own-write visibility')])
        for node_id,e in [('update',u),('read',r)]:
            contract['nodes'].append(dict(id=node_id,queries=[e['sql']],uses={'pk':'int64',**({'quantity':'int32'} if node_id=='update' else {})},defines={},
                body_known=True,transaction='original_outer_read_committed',
                effects=dict(reads=['partner_stockrecord'],writes=['partner_stockrecord'] if node_id=='update' else [],locks=['partner_stockrecord'] if node_id=='update' else []),
                java_calls=dict(base=1,terms={}),database=None,
                requires=['preserve_statement_order','original_error_and_lock_semantics','complete_return_rows_for_original_materialization'],
                retention_reason='Database-side experiment is not a verified Python result handoff/backend',
                source_stack=e['stack']))
        save('sql-boundary-contract.json',contract)
        selection=select_complete_contract(contract);save('sql-boundary-selection.json',selection)
        check('same DP retains two calls without host ABI/backend proof',selection['selection']['selected_calls']==dict(base=2,terms={}) and not selection['selection']['database_regions'])
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres'];report['image']=image
        check('same pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-allocation-contract',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-allocation-contract',
            '--memory=1024m','--cpus=2','--mount','type=volume,src='+name+',dst=/var/lib/postgresql',
            '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-oscar-contract-admin',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-oscar-contract-only'",'role.log')
        sql('CREATE ROLE oscar_restricted WITH NOSUPERUSER NOBYPASSRLS NOLOGIN','restricted-role.log')
        sql('GRANT oscar_restricted TO oscar_user','membership.log')
        sql('CREATE DATABASE oscar_contract OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:
            run(DOCKER+['exec','-i',name,'psql','-U','oscar_user','-d','oscar_contract','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',TZ='UTC',
            PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),DJANGO_SETTINGS_MODULE='settings',
            DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_PORT=host.split(':')[1],
            DATABASE_NAME='oscar_contract',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-oscar-contract-only')
        print('Running fixed original-host and three-arm SQL contract cases',flush=True)
        run([PYTHON,probe,out/'database'],'probe.log',env=env)
        result=json.loads((out/'database/result.json').read_text())
        check('fixed eleven three-arm database cases passed',result['status']=='passed' and len(result['cases'])==11)
        check('original application not replaced',result['actual_oscar_replacement'] is False)
        report.update(status='passed',database_checks=len(result['checks']),database_cases=11,host_cases=3,lock_cases=2)
        save('decision.json',dict(admitted=False,actual_database_regions=0,
            rejected='Direct UPDATE RETURNING fails AFTER-trigger and BEFORE-trigger-skipped-update controls',
            supported='Ordered invoker server UPDATE then SELECT matches 11 local SQL cases, including errors and column privileges; operation retains row lock until outer rollback',
            host_findings='Original refresh creates a distinct partial model and updates the original object; original signals and relation aliases are observable',
            next='Test a source-bound result handoff through the original Django materializer/refresh; preserve missing-row error and all original callbacks. Only then consider a verified capability for the unchanged selector.',
            no_claim=['complete application equivalence','general custom managers/routers/extensions','all concurrency or exception diagnostics','latency or throughput speedup']))
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
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),database_checks=report.get('database_checks'))),flush=True)


if __name__=='__main__':main()
