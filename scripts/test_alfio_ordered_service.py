#!/usr/bin/env python3
"""Isolated sequential server lookup mechanism test; no application deployment."""
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

BASE=ROOT/'evidence/alfio-service-join-audit/20261106-11a212bff8f2'
PREVIOUS=ROOT/'evidence/alfio-service-groups/20261106-6d78cea0bd77'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'


def main():
    out=ROOT/'evidence/alfio-ordered-service'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-ordered-service-'+out.name
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
        probe=ROOT/'prototype/alfio/AlfioOrderedServiceProbe.java';join=ROOT/'prototype/alfio/ordered_service_lookup.sql'
        helper=ROOT/'prototype/alfio/AlfioServiceJoinAudit.java'
        source=ROOT/'work/alfio/source-d44407944e58/src/main/java'
        files=[Path(__file__).resolve(),probe,helper,join,ROOT/'work/paperless/images.lock.json']
        files += [ROOT/'scripts'/n for n in ['alfio_baseline.py','placement_frontend.py','fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        files += [source/n for n in ['alfio/manager/support/reservation/ReservationCostCalculator.java',
            'alfio/manager/TicketReservationManager.java','alfio/config/DataSourceConfiguration.java','alfio/config/RoleAndOrganizationsTransactionPreparer.java',
            'alfio/repository/AdditionalServiceRepository.java','alfio/repository/AdditionalServiceItemRepository.java']]
        for p in files:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
            report['inputs'][str(p.relative_to(ROOT))]=digest(dest)
        fixture=retain(BASE,'final-database.sql')
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
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe,helper],'compile.log')
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']
        check('locked PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=ordered-service',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=ordered-service',
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
        # The uninstrumented function is retained separately from barrier/control variants.
        function_sql=join.read_text()
        barrier=function_sql.replace('ordered_service_lookup','ordered_service_lookup_barrier').replace(
            '-- TEST_BARRIER_BEFORE_LOOKUP','IF step = 2 THEN PERFORM pg_advisory_xact_lock(73006011); END IF;')
        stable=barrier.replace('ordered_service_lookup_barrier','ordered_service_lookup_stable').replace('LANGUAGE plpgsql VOLATILE','LANGUAGE plpgsql STABLE')
        for label,body in [('function',function_sql),('barrier',barrier),('stable-control',stable)]:
            path=out/(label+'.sql');path.write_text(body)
            with path.open('rb') as stream:run(DOCKER+['exec','-i',name,'psql','-U','alfio_user','-d','alfio','-X','-v','ON_ERROR_STOP=1'],label+'-install.log',stdin=stream)
        save('function-properties.json',json.loads(sql("select json_agg(t) from (select proname,provolatile,prosecdef,pg_get_userbyid(proowner) owner from pg_proc where pronamespace='reversedb_probe'::regnamespace order by proname) t",'function-properties.log')))
        save('schema-constraints.json',json.loads(sql("select json_agg(t) from (select conname,convalidated,pg_get_constraintdef(oid) definition from pg_constraint where conrelid in ('additional_service_item'::regclass,'additional_service'::regclass,'event'::regclass) order by conname) t",'constraints.log')))
        save('view.json',json.loads(sql("select json_build_object('definition',pg_get_viewdef('additional_service_with_currency'::regclass,true),'owner',pg_get_userbyid(relowner),'options',reloptions) from pg_class where oid='additional_service_with_currency'::regclass",'view.log')))
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log')
        if not port.startswith('127.0.0.1:') or '\n' in port:raise ValueError('Unexpected bind')
        report['database_runs']=1
        run([JDK/'java','-Xmx384m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC','-cp',str(classes)+os.pathsep+cp,
             'research.alfio.AlfioOrderedServiceProbe','jdbc:postgresql://'+port+'/alfio?sslmode=disable',out],'probe.log')
        result=json.loads((out/'result.json').read_text())
        check('real sequential lookup checks passed',result['status']=='passed')
        properties=json.loads((out/'function-properties.json').read_text())
        check('all functions execute with caller privileges',all(not p['prosecdef'] and p['owner']=='alfio_user' for p in properties))
        check('VOLATILE helper and STABLE control explicitly distinguished',{p['proname']:p['provolatile'] for p in properties}=={
            'ordered_service_lookup':'v','ordered_service_lookup_barrier':'v','ordered_service_lookup_stable':'s'})
        run(DOCKER+['exec',name,'pg_dump','-U','postgres','-d','alfio','--no-owner','--no-privileges'],'final-database.sql')
        save('admission.json',dict(admitted=False,algorithm='unchanged ordered-placement-dp/v1',
            mechanism='original host item read and grouping, one VOLATILE invoker call for the supplied ordered definition keys',
            supported_in_test=['full definition rows in supplied order; original view retained',
                'controlled READ COMMITTED mixed versions and REPEATABLE READ old versions match original',
                'empty invoker skips service permission check; nonempty permission failure and RLS retained',
                'missing cardinality returns stop marker; host reconstructs original EmptyResultDataAccessException without aborting SQL transaction'],
            counterexample='raw JSON defers Java enum/model mapping: schema-valid bad enum stops original before second query but helper executes it',
            unresolved=['per-query original model mapping/validation timing','complete Java return and identity ABI',
                'all SQL error classes, diagnostics and failure prefixes','full concurrency and role policy coverage','shared lowering and SQL support'],
            call_model='1 if G=0, otherwise 2 client calls; compared with original 1+G; no performance measurement or optimizer admission',
            no_changes=['original optimizer','application isolation','default application','business data filtering'],
            next='discuss explicit normal-data mapping contract versus preserving original per-query materialization before any backend admission'))
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
