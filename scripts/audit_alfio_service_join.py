#!/usr/bin/env python3
"""One isolated DB audit of relationalization obligations, never a deployment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
import hashlib
from urllib.parse import unquote, urlparse
from alfio_baseline import ROOT,WORK,JDK,JAR,SHA,DOCKER,digest
from placement_frontend import select_complete_contract

BASE=ROOT/'evidence/alfio-roundtrip/20261106-17b16f1a21eb'
PREVIOUS=ROOT/'evidence/alfio-service-groups/20261106-6d78cea0bd77'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'


def main():
    out=ROOT/'evidence/alfio-service-join-audit'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-join-audit-'+out.name
    report=dict(status='running',checks=[],inputs={},retained_assets={},commands=[],cleanup_errors=[],database_runs=0,timing_samples=0)
    resources=[]
    def save(file,obj):(out/file).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def check(label,ok):
        if not ok:raise AssertionError(label)
        report['checks'].append(label)
    def retain(folder,file):
        p=folder/file
        if digest(p)!=json.loads((folder/'manifest.json').read_text())[file]:raise AssertionError(p)
        report['retained_assets'][str(p)]=digest(p);return p
    def run(command,log,stdin=None,required=True,timeout=120):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(query,log,database='alfio'):
        return run(DOCKER+['exec',name,'psql','-U','postgres','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)
    try:
        probe=ROOT/'prototype/alfio/AlfioServiceJoinAudit.java';join=ROOT/'prototype/alfio/service_group_join_diagnostic.sql'
        source=ROOT/'work/alfio/source-d44407944e58/src/main/java'
        files=[Path(__file__).resolve(),probe,join,ROOT/'work/paperless/images.lock.json']
        files += [ROOT/'scripts'/n for n in ['alfio_baseline.py','placement_frontend.py','fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        files += [source/n for n in ['alfio/manager/support/reservation/ReservationCostCalculator.java',
            'alfio/manager/TicketReservationManager.java','alfio/config/DataSourceConfiguration.java','alfio/config/RoleAndOrganizationsTransactionPreparer.java',
            'alfio/repository/AdditionalServiceRepository.java','alfio/repository/AdditionalServiceItemRepository.java']]
        for p in files:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
            report['inputs'][str(p.relative_to(ROOT))]=digest(dest)
        fixture=retain(BASE,'fixture.sql')
        contract=json.loads(retain(PREVIOUS,'service-group-contract.json').read_text())
        old_result=json.loads(retain(PREVIOUS,'service-group-result.json').read_text())
        check('same selector and contract keep original selection',select_complete_contract(contract)==old_result)
        for n in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged core '+n,digest(ROOT/'scripts'/n)==digest(retain(FIRST,'inputs/scripts/'+n)))
        for n in ['neworder','payment']:
            c=json.loads(retain(FIRST,n+'-contract.json').read_text());r=json.loads(retain(FIRST,n+'-selection.json').read_text())
            check(n+' selection unchanged',select_complete_contract(c)['selection']==r)
        check('official jar',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        binary=WORK/'official-binary';original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    if digest(binary/member)!=hashlib.sha256(jar.read(member)).hexdigest():raise AssertionError(member)
        check('complete official runtime matches jar',True)
        cp=os.pathsep.join(map(str,[original,*libs]));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe],'compile.log')
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']
        check('locked PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=service-join-audit',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=service-join-audit',
            '--memory=1024m','--cpus=2','--mount','type=volume,src='+name+',dst=/var/lib/postgresql',
            '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-join-admin-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(40):
            # The image's temporary init server listens only on a Unix socket.
            # Wait for TCP so initialization cannot shut down under the test.
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('DB readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-join-audit-only'",'role.log','postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user','database.log','postgres')
        with fixture.open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','alfio_user','-d','alfio','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        sql("CREATE ROLE probe_items_only WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-join-audit-only'; GRANT USAGE ON SCHEMA public TO probe_items_only; GRANT SELECT ON additional_service_item TO probe_items_only",'restricted-role.log')
        save('schema-constraints.json',json.loads(sql("select json_agg(t) from (select conname,convalidated,pg_get_constraintdef(oid) definition from pg_constraint where conrelid in ('additional_service_item'::regclass,'additional_service'::regclass,'event'::regclass) order by conname) t",'constraints.log')))
        save('view.json',json.loads(sql("select json_build_object('definition',pg_get_viewdef('additional_service_with_currency'::regclass,true),'owner',pg_get_userbyid(relowner),'options',reloptions) from pg_class where oid='additional_service_with_currency'::regclass",'view.log')))
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log')
        if not port.startswith('127.0.0.1:') or '\n' in port:raise ValueError('Unexpected bind')
        report['database_runs']=1
        run([JDK/'java','-Xmx384m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC','-cp',str(classes)+os.pathsep+cp,
             'research.alfio.AlfioServiceJoinAudit','jdbc:postgresql://'+port+'/alfio?sslmode=disable',out,join],'probe.log')
        result=json.loads((out/'result.json').read_text());origin=urlparse(result['origin'])
        check('original calculator used',origin.scheme=='file' and not origin.netloc and Path(unquote(origin.path)).resolve()==original.resolve())
        check('real database checks passed',result['status']=='passed')
        run(DOCKER+['exec',name,'pg_dump','-U','postgres','-d','alfio','--no-owner','--no-privileges'],'final-database.sql')
        missing=json.loads((out/'missing-definition.json').read_text())
        save('admission.json',dict(admitted=False,algorithm='unchanged ordered-placement-dp/v1',
            candidate='single LEFT JOIN diagnostic, with original view and full JSON rows; not an executable lowering',
            proved_in_test=['stable full-row bag equality with all original columns','primary-key view lookup gives at most one definition per event/service key',
                'same non-superuser role and RLS deny-all control retain empty visibility'],
            counterexamples=['READ COMMITTED original can observe mixed definition versions; a single join sees a single statement snapshot',
                'inner join silently drops legal cross-event FK references',
                'empty original needs no definition SELECT privilege but JOIN does (negative permission control)'],
            original_missing_definition=missing['original_outcome'],
            unresolved=['group and item order reconstruction; SQL has no ORDER BY',
                'original exception and first-failure order across all cases',
                'one original definition object per group and complete model construction ABI',
                'full original role/RLS policy space, concurrency and side-effect compatibility',
                'shared SQL parser and lowering coverage'],
            forbidden_shortcuts=['no added isolation level in production','no assumed cross-event composite FK','no row deletion, deduplication or privilege elevation'],
            next='keep candidate unadmitted; discuss a stable-snapshot workload contract or a mechanism preserving sequential effects before implementation'))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container':
                    run(DOCKER+['logs',name],'postgres.log',required=False)
                    run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else:run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error:report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:report['status']='failed'
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)
    if report['status']!='passed':raise RuntimeError('Audit incomplete')


if __name__=='__main__':main()
