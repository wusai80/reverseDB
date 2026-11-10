#!/usr/bin/env python3
"""Final bounded real-DB selection/reread audit; no further candidate expansion."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
from urllib.parse import unquote,urlparse
from alfio_baseline import ROOT,WORK,JDK,JAR,SHA,DOCKER,digest
from placement_frontend import select_complete_contract
from partial_placement import select_partial_contract

BASE=ROOT/'evidence/alfio-roundtrip/20261106-17b16f1a21eb'
PREVIOUS=ROOT/'evidence/alfio-shared-method/20261106-0a751681f789'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
BILLING=ROOT/'evidence/alfio-billing-audit/20261106-046e6adb3252'


def main():
    out=ROOT/'evidence/alfio-final-selection'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-alfio-final-'+out.name;resources=[]
    report=dict(status='running',checks=[],inputs={},retained_assets={},commands=[],database_runs=0,timing_samples=0,cleanup_errors=[])
    def save(file,obj):(out/file).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def check(label,ok):
        if not ok:raise AssertionError(label)
        report['checks'].append(label)
    def retain(base,file):
        p=base/file;assert digest(p)==json.loads((base/'manifest.json').read_text())[file]
        report['retained_assets'][str(p)]=digest(p);return p
    def run(command,log,stdin=None,required=True):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(query,log,db='alfio'):
        return run(DOCKER+['exec',name,'psql','-U','postgres','-d',db,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)
    try:
        probe=ROOT/'prototype/alfio/AlfioFinalSelectionProbe.java'
        files=[Path(__file__).resolve(),probe,ROOT/'work/paperless/images.lock.json']+[ROOT/'scripts'/n for n in
            ['alfio_baseline.py','partial_placement.py','placement_frontend.py','fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        for p in files:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst);report['inputs'][str(p.relative_to(ROOT))]=digest(p)
        fixture=retain(BASE,'fixture.sql')
        category=json.loads(retain(PREVIOUS,'category_body-contract.json').read_text())
        locked=json.loads(retain(PREVIOUS,'locked_selection-contract.json').read_text())
        prior=json.loads(retain(PREVIOUS,'category_body-application.json').read_text())
        check('source-bound selection unchanged',select_partial_contract(category)==prior)
        check('original selected helper has two calls',select_complete_contract(locked)['selection']['original_calls']==dict(base=2,terms={}))
        for n in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged original '+n,digest(ROOT/'scripts'/n)==digest(retain(FIRST,'inputs/scripts/'+n)))
        for n in ['neworder','payment']:
            c=json.loads(retain(FIRST,n+'-contract.json').read_text())
            check(n+' exact original selection',select_complete_contract(c)['selection']==json.loads(retain(FIRST,n+'-selection.json').read_text()))
        billing=json.loads(retain(BILLING,'contract.json').read_text());billing_result=json.loads(retain(BILLING,'selection.json').read_text())
        check('billing host-clock boundary unchanged',select_complete_contract(billing)==billing_result)
        check('pinned official JAR',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        binary=WORK/'official-binary';original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary/member)==hashlib.sha256(jar.read(member)).hexdigest(),member
        check('official runtime and classpath verified',True)
        cp=os.pathsep.join(map(str,[original,*libs]));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe],'compile.log')
        save('protocol.json',dict(scope='last bounded contract test of selection, quantity guard and category reread',
             cases=['bounded selection','unbounded selection','waiting statuses','competing locks and release','actual quantity failure prefix',
                    'stable full-category models','READ COMMITTED concurrent category change','REPEATABLE READ control','concurrent category deactivation'],
             actual_code='official manager helper and outer reserveTicketsForCategory, original NPJT with original custom converters/mappers',
             boundary='outer method stops via test sentinel after final category query and mapping; includes an earlier fixToken read outside the target three-call interval',
             transactions='explicit local caller transactions; no Spring proxy or HTTP replay; source transaction policy unchanged',
             concurrency='writer commit invoked synchronously before final category SELECT; locks use separate real connections and NOWAIT, no sleep-based interleaving',
             data='same full 63-table fixture restored; synthetic row updates rolled back or restored; full tables and sequences compared afterward',
             stop='one fixed nine-case matrix; no added performance test, new candidate generation, deletion or workload-domain restriction'))
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres'];report['image']=image
        check('locked PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=alfio-final-selection',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=alfio-final-selection','--memory=1024m','--cpus=2',
             '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-final-admin-only',image['resolved'],
             '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('Database readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-final-selection-only'",'role.log','postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user','database.log','postgres')
        with fixture.open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','alfio_user','-d','alfio','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log');assert port.startswith('127.0.0.1:') and '\n' not in port
        report['database_runs']=1
        run([JDK/'java','-Xmx384m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC','-cp',str(classes)+os.pathsep+cp,
             'research.alfio.AlfioFinalSelectionProbe','jdbc:postgresql://'+port+'/alfio?sslmode=disable&socketTimeout=15',out],'probe.log')
        result=json.loads((out/'result.json').read_text());origin=urlparse(result['origin'])
        check('original manager executed',origin.scheme=='file' and Path(unquote(origin.path)).resolve()==original.resolve())
        check('fixed nine real database cases passed',result['status']=='passed' and len(result['cases'])==9)
        runtime=json.loads((out/'runtime.json').read_text())
        check('default isolation was not narrowed',runtime['default_isolation']==runtime['isolation']=='read committed')
        before=json.loads((out/'before-full-state.json').read_text());after=json.loads((out/'after-full-state.json').read_text())
        check('complete fixture state and sequences restored',before==after and len(before['tables'])==63)
        check('all original tickets retained',len(json.loads(after['tables']['ticket']))==20)
        # Make the demonstrated barriers explicit; do not manufacture an admitted backend.
        refined=copy.deepcopy(category)
        for n in refined['nodes']:
            if n['id'] in ['select_locked_tickets','quantity_guard','reread_category']:
                n['requires']+=['preserve_skip_locked_selection_and_caller_lock_lifetime','preserve_quantity_failure_before_reread',
                                'preserve_each_statement_snapshot_and_category_mapping','preserve_reread_missing_row_exception']
                n['host_retention']=dict(required=True,reason='Final audit retains original ordered selection/guard/reread; no equivalence proof for a replacement')
        new_result=select_partial_contract(refined);save('category-contract.json',refined);save('category-application.json',new_result)
        check('whole selector still refuses an invented global plan',new_result['whole']==prior['whole'])
        check('original local choices unchanged',[i['result'] for i in new_result['islands']]==[i['result'] for i in prior['islands']])
        for node_id in ['select_locked_tickets','quantity_guard','reread_category']:
            fake=copy.deepcopy(refined);n=next(n for n in fake['nodes'] if n['id']==node_id)
            n['database']=dict(lowering='synthetic-claim-only',verified_source_contract=True,guarantees=n['requires'],transaction_policy='same_outer_transaction')
            check(node_id+' remains protected from claimed replacement',select_partial_contract(fake)['status']=='retention_conflict_no_partial_plan')
        save('admission.json',dict(admitted=False,actual_database_regions=0,
             findings=['category reread is observable under original READ COMMITTED; earlier category reuse changes full result',
                       'concurrent deactivation makes original reread throw; cached reuse loses exception',
                       'SKIP LOCKED selection and transaction-held row locks verified with competing connections',
                       'original quantity guard stops before reread, and partial selection locks persist until caller rollback'],
             limits=['not a proof against every future sequential server implementation','REPEATABLE READ is a control, not an allowed production isolation change',
                     'no optimized full runtime exists here; no performance claims','local real-code boundary test, not full Spring/HTTP equivalence'],
             decision='close alf.io as a transfer-boundary/negative case under current conservative method; stop additional candidate expansion and timing'))
        report.update(status='passed',database_cases=len(result['cases']),database_checks=len(result['checks']),actual_database_regions=0)
        for p,sha in report['retained_assets'].items():assert digest(Path(p))==sha
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
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),database_cases=report.get('database_cases'),database_checks=report.get('database_checks'))),flush=True)
    if report['status']!='passed':raise RuntimeError('Final audit incomplete')


if __name__=='__main__':main()
