#!/usr/bin/env python3
"""One fixed admission audit and concurrency matrix for the existing candidate."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from placement_frontend import select_complete_contract

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/oscar-baseline/20261106-ed9f704d86d6'
PRIOR=ROOT/'evidence/oscar-handoff/20261107-64466eabd344'
CONTRACT=ROOT/'evidence/oscar-allocation-contract/20261107-979627b9d9bb'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE=ROOT/'work/oscar/source-a5ed3c9c'
PYTHON=ROOT/'work/oscar/venv/bin/python'
DOCKER=['docker','--context','desktop-linux']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out=ROOT/'evidence/oscar-concurrency'/('20261107-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-oscar-concurrency-'+out.name;resources=[]
    report=dict(status='running',checks=[],commands=[],cleanup_errors=[],retained_assets={},performance_claim=None)
    def save(n,o):(out/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
    def check(n,ok):
        assert ok,n
        report['checks'].append(n)
    def load(p):return json.loads(p.read_text())
    def run(command,log,stdin=None,required=True,env=None,timeout=180):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q,log):return run(DOCKER+['exec',name,'psql','-U','postgres','-d','postgres','-XAt','-v','ON_ERROR_STOP=1','-c',q],log)
    try:
        def retain(base,f):
            p=base/f;assert sha(p)==load(base/'manifest.json')[f],f
            report['retained_assets'][str(p)]=sha(p);return p
        fixture=retain(BASE,'fixture.sql')
        old=retain(PRIOR,'inputs/prototype/oscar/allocation_handoff.py')
        binding=retain(PRIOR,'binding.json');shutil.copy2(binding,out/'binding.json')
        for f,h in load(retain(BASE,'source-manifest.json')).items():assert sha(SOURCE/f)==h,f
        for n,info in load(retain(BASE,'a/loaded-sources.json')).items():assert sha(Path(info['path']))==info['sha256'],n
        check('pinned application/framework/driver unchanged',True)
        for p in [Path(__file__).resolve(),ROOT/'prototype/oscar/concurrency_probe.py',ROOT/'prototype/oscar/allocation_handoff.py',ROOT/'prototype/oscar/baseline_probe.py']:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('first-project core unchanged '+f,sha(ROOT/'scripts'/f)==sha(retain(FIRST,'inputs/scripts/'+f)))
        for case in ['neworder','payment']:
            check(case+' original selection unchanged',select_complete_contract(load(retain(FIRST,case+'-contract.json')))['selection']==load(retain(FIRST,case+'-selection.json')))
        contract=load(retain(CONTRACT,'sql-boundary-contract.json'))
        selection=select_complete_contract(contract);save('still-retained-selection.json',selection)
        check('no capability admitted by limited concurrency tests',selection==load(retain(CONTRACT,'sql-boundary-selection.json')))
        # Source evidence distinguishes user-facing error text from diagnostic logging.
        source=SOURCE/'src/oscar/apps/checkout/views.py';text=source.read_text();tree=ast.parse(text)
        method=next(m for c in tree.body if isinstance(c,ast.ClassDef) for m in c.body
                    if isinstance(m,ast.FunctionDef) and m.name=='submit')
        handlers=[h for h in ast.walk(method) if isinstance(h,ast.ExceptHandler) and isinstance(h.type,ast.Name) and h.type.id=='Exception']
        handler=handlers[-1];snippet=ast.get_source_segment(text,handler)
        check('checkout generic error uses fixed user message and logs exception',
              'A problem occurred while placing this order. Please contact customer services.' in snippet and 'logger.exception' in snippet and 'self.restore_frozen_basket()' in snippet)
        save('diagnostic-observation.json',dict(source=str(source),sha256=sha(source),method=method.name,line=handler.lineno,handler=snippet,
            conclusion='For the generic exception handler, source sends a constant preview error and logs the exception; full text remains observable in logs and direct API callers.',
            limits='Source audit, not a new HTTP checkout execution; no normalization of DataError or log output, no narrowed exception contract.'))
        # Thread lifecycle fix changes the same adapter: rerun the existing fixed
        # host/order matrix once, without changing its cases or tolerances.
        reuse=os.environ.get('RDB_REUSE_SERIAL')
        if reuse:
            reg=Path(reuse)
            for f,h in load(reg/'manifest.json').items():
                assert sha(reg/f)==h,f
                if f.startswith('inputs/') and f.endswith('.py'):assert sha(ROOT/f[len('inputs/'):])==h,f
            check('reused passing serial matrix with unchanged implementation hashes',load(reg/'run.json')['status']=='passed')
        else:
            print('Checking existing host/order matrix after lifecycle fix',flush=True)
            log=run([sys.executable,'-B',ROOT/'scripts/test_oscar_handoff.py'],'serial-regression.log',timeout=240)
            regression=json.loads(log.splitlines()[-1]);check('existing fixed differential matrix still passes',regression['status']=='passed')
            reg=Path(regression['output'])
        report['serial_regression']=str(reg)
        for f in ['run.json','comparison.json','manifest.json']:
            report['retained_assets'][str(reg/f)]=sha(reg/f)
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres'];report['image']=image
        check('same pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-concurrency',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-concurrency','--memory=1024m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-concurrency-admin',
            image['resolved'],'-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-concurrency-only'",'role.log')
        sql('CREATE ROLE oscar_noexecute WITH NOSUPERUSER NOBYPASSRLS NOLOGIN','noexecute-role.log')
        sql('CREATE DATABASE oscar_concurrency OWNER oscar_user','database.log')
        with fixture.open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','oscar_user','-d','oscar_concurrency','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',TZ='UTC',PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
            DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_PORT=host.split(':')[1],
            DATABASE_NAME='oscar_concurrency',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-concurrency-only',
            RDB_HANDOFF_BINDING=str(binding),RDB_OLD_HANDOFF=str(old))
        print('Running three fixed original/candidate interleavings',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/concurrency_probe.py','audit',out/'database'],'probe.log',env=env)
        result=load(out/'database/result.json')
        check('fixed three concurrency cases completed',result['status']=='passed' and len(result['cases'])==3)
        check('all fixture rows and sequences restored',load(out/'database/before-full-state.json')==load(out/'database/after-full-state.json')==load(BASE/'fixture-full-state.json'))
        save('decision.json',dict(status='bounded concurrent candidate verified; no general admission',
            fixed='overlapping experimental contexts now share one process hook installation, separate per-context result packets, and restore only after the last user exits',
            verified=['each worker really uses one candidate call and consumes its own rows','same-row competing writer blocks and observes serialized cumulative allocation',
                      'zero-row UPDATE followed by concurrent INSERT remains visible to the later SELECT inside the volatile function'],
            diagnostic='generic checkout handler uses a fixed user message, but detailed error text is logged; full diagnostic difference retained',
            prerequisites=['pinned interpreter/code/driver and verified source call sites','correct public relation/schema and original transaction',
                           'verified invoker function definition and EXECUTE privilege','unknown custom managers/routers/cursors or schema changes remain outside admission'],
            stop='fixed local concurrency/admission audit complete; no more schedule expansion, error-text rewriting or performance measurement in this step',
            algorithm='original DP/core and retained Oscar selection unchanged',performance_claim=None,default_application_modified=False))
        report.update(status='passed',database_checks=len(result['checks']),concurrency_cases=3)
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
