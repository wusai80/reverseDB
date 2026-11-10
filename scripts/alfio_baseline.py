#!/usr/bin/env python3
"""Pinned official alf.io artifact: original service baseline and bytecode feasibility."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work/alfio'
SOURCE=WORK/'source-d44407944e58'
JDK=WORK/'jdk17/jdk-17.0.21+9/Contents/Home/bin'
DOCKER=['docker','--context','desktop-linux']
JAR=WORK/'alfio-2.0-M5-2609-boot.jar'
SHA='c769aa49fbd25ce8c348d78e4946b759dfa318bb7bffc0521378b78189176f39'
COMMIT='d44407944e5875bfaa3dd0cff6f4e42214f34931'
TARGETS=['alfio/manager/TicketReservationManager','alfio/repository/TicketRepository','alfio/repository/TicketReservationRepository','alfio/config/DataSourceConfiguration','alfio/config/RoleAndOrganizationsTransactionPreparer']


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out=ROOT/'evidence/alfio-baseline'/('20261106-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-alfio-'+out.name
    report=dict(status='running',commands=[],inputs={},retained_assets={},decompilation={},cleanup_errors=[])
    resources=[]; env=dict(os.environ,ALFIO_DB_PASSWORD='synthetic-alfio-only')
    def save(file,value): (out/file).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(cmd,log,check=True,timeout=180):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).parent.mkdir(parents=True,exist_ok=True); (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return p.returncode,p.stdout.decode(errors='replace').strip()
    def sql(query,log,database='alfio'):
        return run(DOCKER+['exec',name,'psql','-U','postgres','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)[1]
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT); dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dest)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dest)
    try:
        assert digest(JAR)==SHA
        report['retained_assets'][str(JAR)]=SHA
        assetlock=ROOT/'prototype/alfio/assets.lock.json'
        assets=json.loads(assetlock.read_text())
        archive=WORK/'source-d44407944e58.tar.gz'
        assert digest(archive)==assets['source_archive']['sha256']
        report['retained_assets'][str(archive)]=digest(archive)
        tool=ROOT/'work/tpcc/tools/vineflower-1.12.0/vineflower-1.12.0.jar'
        assert digest(tool)=='1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d'
        report['retained_assets'][str(tool)]=digest(tool)
        probe=ROOT/'prototype/alfio/AlfioBaselineProbe.java'; lock=ROOT/'work/paperless/images.lock.json'
        for path in (Path(__file__).resolve(),probe,lock,assetlock,SOURCE/'README.md',SOURCE/'build.gradle',SOURCE/'gradle/wrapper/gradle-wrapper.properties',
                     SOURCE/'src/test/java/alfio/test/util/IntegrationTestUtil.java',SOURCE/'src/test/java/alfio/manager/TicketReservationManagerIntegrationTest.java'):
            freeze(path)
        for file in ['java','javac','javap']: report['retained_assets'][str(JDK/file)]=digest(JDK/file)
        save('protocol.json',dict(project='alf.io',release='2.0-M5-2609',commit=COMMIT,official_jar_sha256=SHA,
          scope='original Spring service entry for reservation lifecycle; original HTTP info endpoint only; no optimized implementation',
          execution='fixed twelve serial operations (including three explicit waiting-queue processing steps); twenty-ticket fixture adapted from upstream tests; no pre-existing data removed',
          clock='ClockProvider fixed at 2026-11-06T10:00:00Z; legacy new Date/UUID remain original, values recorded; no byte-for-byte replay claim',
          preservation='official business classes, actual Spring transactions and NPJT proxies, non-superuser/no-BYPASSRLS, full schema, WAL/fsync; timer-driven jobs disabled via upstream profile, required waiting-queue processor invoked explicitly',
          checks=['HTTP info 200','full schema migration','committed inventory','insufficient-stock all-public-table rollback','cancel/re-reserve','explicit organization RLS policy','original class origins'],
          decompilation='Vineflower 1.12.0, five targets plus nested classes, binary classes/libraries only; raw output never patched; recompile is not equivalence',
          stop='one original fixture and one decompile pass; preserve failures; no optimization, performance or claim of full HTTP authorization/concurrency'))
        binary=WORK/'official-binary/BOOT-INF'; classes=binary/'classes'
        # Check every extracted input against its source bytes inside the verified official JAR.
        with zipfile.ZipFile(JAR) as z:
            index=z.read('BOOT-INF/classpath.idx').decode()
            (out/'classpath.idx').write_text(index)
            library_names=[json.loads(line[2:]) for line in index.splitlines() if line]
            assert len(library_names)==len(set(library_names))
            assert all(x.startswith('BOOT-INF/lib/') and x.endswith('.jar') and '..' not in Path(x).parts for x in library_names)
            libs=[WORK/'official-binary'/x for x in library_names]
            report['library_order']='official BOOT-INF/classpath.idx, excluding non-runtime jarmode tools'
            for member in z.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    p=WORK/'official-binary'/member
                    assert digest(p)==hashlib.sha256(z.read(member)).hexdigest(),member
        cp=os.pathsep.join(map(str,[classes,*libs])); empty=out/'empty'; empty.mkdir()
        run([JDK/'java','-version'],'java-version.log')
        probeclasses=out/'probe-classes'; probeclasses.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',probeclasses,probe],'compile-probe.log')
        target=out/'bytecode-targets'; target.mkdir(); sources=out/'decompiled'; sources.mkdir()
        for selected in TARGETS:
            for p in (classes/selected).parent.glob(Path(selected).name+'*.class'):
                if p.stem.split('$')[0]!=Path(selected).name: continue
                dst=target/p.relative_to(classes); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
        code,_=run([JDK/'java','-Xmx1024m','-XX:ActiveProcessorCount=2','-jar',tool,'--thread-count=2','--folder','--bytecode-source-mapping=true','--skip-extra-files=true',
            *['-e='+str(p) for p in [classes,*libs]],target,sources],'decompile.log',check=False)
        report['decompilation']['returncode']=code
        report['decompilation']['targets']={}
        for selected in TARGETS:
            cls=selected.replace('/','.'); key=Path(selected).name
            run([JDK/'javap','-p','-v','-classpath',cp,cls],'javap/'+key+'.log')
            source=sources/(selected+'.java'); compiled=out/'recompiled'/key; compiled.mkdir(parents=True)
            if source.exists():
                code,_=run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',compiled,source],'recompile/'+key+'.log',check=False)
            else: code=None
            report['decompilation']['targets'][cls]=dict(source_emitted=source.exists(),raw_recompile_returncode=code)
        print('Bytecode feasibility: '+json.dumps(report['decompilation']),flush=True)
        image=json.loads(lock.read_text())['images']['postgres']; report['image']=image
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')[1]==image['id']
        run(DOCKER+['volume','create','--label','reversedb.experiment=alfio-baseline',name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=alfio-baseline','--memory=1536m','--cpus=2',
            '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-admin-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','postgres'],f'ready-{i}.log',check=False)[1]: break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'",'create-role.log','postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user','create-database.log','postgres')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log')[1]; assert port.startswith('127.0.0.1:') and '\n' not in port
        url='jdbc:postgresql://'+port+'/alfio?sslmode=disable'
        save('settings.json',json.loads(sql("SELECT json_object_agg(name,setting) FROM pg_settings WHERE lower(name) IN ('fsync','synchronous_commit','full_page_writes','wal_level','row_security','timezone','server_version')",'settings.log')))
        runtime=out/'original'; runtime.mkdir()
        run([JDK/'java','-Xmx1024m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC',f'-Xlog:class+load=info:file={out/"class-load.log"}',
            '-cp',str(probeclasses)+os.pathsep+cp,'research.alfio.AlfioBaselineProbe',url,runtime],'original.log',timeout=240)
        summary=json.loads((runtime/'summary.json').read_text()); assert summary['status']=='passed'
        save('schema.json',json.loads(sql("SELECT json_agg(t ORDER BY tablename) FROM (SELECT schemaname,tablename,rowsecurity FROM pg_tables WHERE schemaname='public') t",'schema.log')))
        save('migrations.json',json.loads(sql('SELECT json_agg(t ORDER BY installed_rank) FROM flyway_schema_history t','migrations.log')))
        # Preserve all data after the experiment, including audit and untouched tables.
        code,_=run(DOCKER+['exec',name,'pg_dump','-U','postgres','-d','alfio','--no-owner','--no-privileges'],'final-database.sql',timeout=120)
        loads=(out/'class-load.log').read_text()
        for cls in ['alfio.manager.TicketReservationManager','alfio.repository.TicketRepository']:
            assert str(classes) in next(l for l in loads.splitlines() if cls+' source:' in l),cls
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        report.update(status='passed',original_baseline=summary,limitations=['service-level business baseline, not full business HTTP flow','RLS SQL policy tested; HTTP tenant-context propagation not tested','random IDs and legacy wall clocks remain','no optimized roundtrip execution','no source build; official release artifact used'])
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container': run(DOCKER+['logs',name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save('run.json',report)
        manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()}; save('manifest.json',manifest)
        assert all(digest(out/p)==sha for p,sha in manifest.items())
        print(json.dumps(dict(status=report['status'],output=str(out),files=len(manifest))),flush=True)
    if report['status']!='passed': raise RuntimeError('Original baseline incomplete')


if __name__=='__main__': main()
