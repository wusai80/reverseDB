#!/usr/bin/env python3
"""Official vs unedited recompiled alf.io classes, identical DB and source entropy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
from alfio_baseline import ROOT, WORK, JDK, DOCKER, JAR, SHA, TARGETS, digest

BASE=ROOT/'evidence/alfio-baseline/20261106-788b831dd41c'


def generated_probe(source):
    def replace(old,new):
        nonlocal source
        assert source.count(old)==1,old
        source=source.replace(old,new)
    replace('public final class AlfioBaselineProbe','public final class AlfioRoundtripProbe')
    replace('output=Path.of(args[1]); Files.createDirectories(output);','output=Path.of(args[1]); Files.createDirectories(output);\n    boolean prepare=args[2].equals("prepare");')
    # HTTP/fixture initialization occurs once; restore its full DB for both arms.
    start=source.index('      var web=')
    end=source.index('      var manager=',start)
    fixture=source[start:end]
    source=source[:start]+'''      if(prepare) {
'''+fixture+'''        inventory(observer,20,0,"prepared");
        return;
      }
      var events=context.getBean(EventManager.class);
      var event=events.getSingleEvent("reversedb-event","reversedb-operator");
      String username="reversedb-operator";
      int org=event.getOrganizationId();
      int category=context.getBean(TicketCategoryRepository.class).findAllTicketCategories(event.getId()).get(0).getId();
'''+source[end:]
    replace('    save(label+"-state.json",result); return result;', '''    save(label+"-state.json",result);
    Map<String,Object> full=new LinkedHashMap<>();
    for(String table:result.keySet()) {
      String quoted="\\\""+table.replace("\\\"","\\\"\\\"")+"\\\"";
      var data=rows(c,"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text COLLATE \\\"C\\\"),'[]'::jsonb)::text data FROM public."+quoted+" t").get(0).get("data");
      full.put(table,JSON.readTree((String)data));
    }
    save(label+"-rows.json",full);
    save(label+"-sequences.json",rows(c,"SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"));
    return result;''')
    operations=[('String a=reserve(', '01-reserve-three'),('var summary=manager.orderSummaryForReservationId','02-query-first'),
      ('try { reserve(manager,event,category,21);','03-insufficient-stock'),('String b=reserve(','04-reserve-two'),
      ('manager.cancelPendingReservation(a,false,username);','05-cancel-first'),
      ('processor.handleWaitingTickets(); inventory(observer,18,2,','06-process-first'),
      ('require(manager.orderSummaryForReservationId(b,event)','07-query-second'),
      ('manager.cancelPendingReservation(b,false,username);','08-cancel-second'),
      ('processor.handleWaitingTickets(); inventory(observer,20,0,"07-','09-process-second'),
      ('String c=reserve(','10-reserve-one'),('manager.cancelPendingReservation(c,false,username);','11-cancel-third'),
      ('processor.handleWaitingTickets(); inventory(observer,20,0,"09-','12-process-third')]
    for marker,label in operations: replace(marker,'ReplayEntropy.begin("'+label+'");\n      '+marker)
    replace('save("02-query.json",Map.of("reservation",a,"tickets",summary.getTicketAmount(),"total",summary.getTotalPrice()));',
      'save("02-query.json",Map.of("reservation",a,"summary",alfio.util.Json.OBJECT_MAPPER.valueToTree(summary)));')
    # Serialize actual complete second result, not the fixture's expected constant.
    replace('require(manager.orderSummaryForReservationId(b,event).getTicketAmount()==2,"second reservation survives first cancellation");\n      save("06-query.json",Map.of("reservation",b,"tickets",2));',
      'var secondSummary=manager.orderSummaryForReservationId(b,event);\n      require(secondSummary.getTicketAmount()==2,"second reservation survives first cancellation");\n      save("06-query.json",Map.of("reservation",b,"summary",alfio.util.Json.OBJECT_MAPPER.valueToTree(secondSummary)));')
    replace('      save("summary.json",', '      save("entropy.json",ReplayEntropy.finish());\n      save("transformations.json",ReplayEntropy.transformations());\n      save("summary.json",')
    return source


def compare_folders(a,b):
    files=sorted(p.name for p in a.glob('*.json') if p.name not in ('loaded-beans.json','transformations.json'))
    other=sorted(p.name for p in b.glob('*.json') if p.name not in ('loaded-beans.json','transformations.json'))
    if files!=other: return dict(equal=False,file_set_mismatch=dict(original=files,recompiled=other))
    differences=[]
    for file in files:
        left=json.loads((a/file).read_text()); right=json.loads((b/file).read_text())
        if left!=right:
            keys=[k for k in sorted(set(left)|set(right)) if left.get(k)!=right.get(k)] if isinstance(left,dict) and isinstance(right,dict) else None
            differences.append(dict(file=file,different_top_level_keys=keys))
    return dict(equal=not differences,compared_files=len(files),differences=differences,
        exclusions={'loaded-beans.json':'arm-specific class origin checked independently','transformations.json':'arm-specific bytecode hashes checked independently'},
        normalization='none; array order, IDs, timestamps, numeric values, NULL, hashes and sequence values compared exactly')


def main():
    out=ROOT/'evidence/alfio-roundtrip'/('20261106-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-alfio-rt-'+out.name; resources=[]
    report=dict(status='running',commands=[],inputs={},retained_assets={},cleanup_errors=[])
    env=dict(os.environ,ALFIO_DB_PASSWORD='synthetic-alfio-only')
    def save(file,value): (out/file).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(cmd,log,check=True,stdin=None,timeout=240):
        cmd=list(map(str,cmd)); report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).parent.mkdir(parents=True,exist_ok=True); (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return p.returncode,p.stdout.decode(errors='replace').strip()
    def sql(query,log,database='alfio'):
        return run(DOCKER+['exec',name,'psql','-U','postgres','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)[1]
    def retain(path):
        assert digest(path)==json.loads((BASE/'manifest.json').read_text())[str(path.relative_to(BASE))]
        report['retained_assets'][str(path)]=digest(path); return path
    def freeze(path):
        dst=out/'inputs'/path.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dst)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dst)
    try:
        assert digest(JAR)==SHA; report['retained_assets'][str(JAR)]=SHA
        for p in [Path(__file__).resolve(),ROOT/'scripts/alfio_baseline.py',ROOT/'prototype/alfio/assets.lock.json',ROOT/'prototype/alfio/ReplayEntropy.java',ROOT/'prototype/alfio/ReplayEntropyWitness.java',ROOT/'work/paperless/images.lock.json']: freeze(p)
        source=retain(BASE/'inputs/prototype/alfio/AlfioBaselineProbe.java')
        generated=out/'generated'; generated.mkdir(); probe=generated/'AlfioRoundtripProbe.java'; probe.write_text(generated_probe(source.read_text()))
        binary=WORK/'official-binary'; original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as z:
            names=[json.loads(line[2:]) for line in z.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            libs=[binary/n for n in names]
            for member in z.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary/member)==__import__('hashlib').sha256(z.read(member)).hexdigest(),member
        candidates=out/'recompiled-classes'; candidates.mkdir()
        for selected in TARGETS:
            directory=BASE/'recompiled'/Path(selected).name
            for p in directory.rglob('*.class'):
                retain(p); dst=candidates/p.relative_to(directory); dst.parent.mkdir(parents=True,exist_ok=True); assert not dst.exists(); shutil.copy2(p,dst)
        for p in candidates.rglob('*.class'): report['inputs']['recompiled/'+str(p.relative_to(candidates))]=digest(p)
        cp=os.pathsep.join(map(str,[original,*libs])); classes=out/'probe-classes'; classes.mkdir(); empty=out/'empty'; empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe,ROOT/'prototype/alfio/ReplayEntropy.java',ROOT/'prototype/alfio/ReplayEntropyWitness.java'],'compile.log')
        manifest=out/'agent-manifest.txt'; manifest.write_text('Premain-Class: research.alfio.ReplayEntropy\n\n')
        agent=out/'entropy-agent.jar'
        run([JDK/'jar','cfm',agent,manifest,'-C',classes,'research/alfio'],'agent-build.log')
        run([JDK/'java','-javaagent:'+str(agent),'-cp',str(classes)+os.pathsep+cp,'alfio.research.ReplayEntropyWitness'],'entropy-witness.log')
        save('protocol.json',dict(arms=['original','recompiled'],targets=TARGETS,classes=sum(1 for _ in candidates.rglob('*.class')),
          candidate='exact archived unedited recompiled class files; same official dependencies in official order',
          fixture='original prepares complete DB once; full dump restored before each arm; HTTP info/setup only in preparation',
          source_control='identical test agent in both arms; scoped to alfio bytecode; active only during fixed 12 service/processor operations; seeded version-4 UUID and fixed legacy Date/currentTimeMillis; complete entropy call record compared',
          equality='exact complete summary JSON, exception type, all public-table rows/hashes and sequences at every captured checkpoint; no volatile-field filtering',
          stop='one fixed pair; bootstrap or genuine differences preserved; no optimization/performance/generalization claim',
          controls='agent witness plus comparator negative controls changing data, reference, time and duplicate count',
          limitations=['service boundary, not complete HTTP authorization','bounded serial fixture, not all program behavior','deterministic entropy is a test intervention','no throughput measurement']))
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']; report['image']=image
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')[1]==image['id']
        run(DOCKER+['volume','create','--label','reversedb.experiment=alfio-roundtrip',name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=alfio-roundtrip','--memory=1536m','--cpus=2',
          '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-admin-only',image['resolved'],
          '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','postgres'],f'ready-{i}.log',check=False)[1]: break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'",'role.log','postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user','prepare-create.log','postgres')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log')[1]; assert port.startswith('127.0.0.1:') and '\n' not in port
        url='jdbc:postgresql://'+port+'/alfio?sslmode=disable'
        save('settings.json',json.loads(sql("SELECT json_object_agg(name,setting) FROM pg_settings WHERE lower(name) IN ('fsync','synchronous_commit','full_page_writes','wal_level','row_security','timezone','server_version')",'settings.log')))
        def replay(arm,phase):
            target=out/arm; target.mkdir()
            path=os.pathsep.join(map(str,[classes,*([candidates] if arm=='recompiled' else []),original,*libs]))
            return run([JDK/'java','-Xmx1024m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC','-javaagent:'+str(agent),f'-Xlog:class+load=info:file={out/(arm+"-classes.log")}',
              '-cp',path,'research.alfio.AlfioRoundtripProbe',url,target,phase],arm+'.log',check=False)[0]
        assert replay('prepare','prepare')==0,'prepare failed'
        run(DOCKER+['exec',name,'pg_dump','-U','postgres','-d','alfio','--no-owner','--no-privileges'],'fixture.sql')
        report['arms']={}
        for arm in ['original','recompiled']:
            sql('DROP DATABASE alfio',arm+'-drop.log','postgres'); sql('CREATE DATABASE alfio OWNER alfio_user',arm+'-create.log','postgres')
            with (out/'fixture.sql').open('rb') as stream:
                run(DOCKER+['exec','-i',name,'psql','-U','alfio_user','-d','alfio','-X','-v','ON_ERROR_STOP=1'],arm+'-restore.log',stdin=stream)
            code=replay(arm,'replay'); report['arms'][arm]={'returncode':code}
            print('Replay '+arm+' exit '+str(code),flush=True)
            if code: continue
            loads=(out/(arm+'-classes.log')).read_text(); expected=candidates if arm=='recompiled' else original
            for selected in TARGETS:
                line=next(l for l in loads.splitlines() if selected.replace('/','.')+' source:' in l)
                assert str(expected) in line,(arm,selected,line)
            report['arms'][arm]['loaded_five_expected_targets']=True
            report['arms'][arm]['checks']=len(json.loads((out/arm/'checks.json').read_text()))
            run(DOCKER+['exec',name,'pg_dump','-U','postgres','-d','alfio','--no-owner','--no-privileges'],arm+'-final.sql')
        if any(a['returncode'] for a in report['arms'].values()): raise RuntimeError('Arm failed; preserve runtime logs')
        comparison=compare_folders(out/'original',out/'recompiled'); save('comparison.json',comparison)
        assert comparison['equal'],comparison
        # Prove the exact comparator catches representative semantic damage.
        controls=out/'negative-controls'; controls.mkdir()
        a=controls/'reference'; b=controls/'mutated'; a.mkdir(); b.mkdir()
        witness={'ticket':[{'id':1,'reservation':'r1','price':1000,'time':'2026-11-06T10:00:00Z'}]}
        (a/'rows.json').write_text(json.dumps(witness)); detected=[]
        for control_name,mutation in [('price',{'price':999}),('reference',{'reservation':'r2'}),('time',{'time':'2026-11-06T10:00:01Z'})]:
            modified={'ticket':[dict(witness['ticket'][0],**mutation)]}; (b/'rows.json').write_text(json.dumps(modified))
            assert not compare_folders(a,b)['equal']; detected.append(control_name)
        (b/'rows.json').write_text(json.dumps({'ticket':witness['ticket']*2})); assert not compare_folders(a,b)['equal']; detected.append('duplicate-row')
        save('negative-controls.json',dict(detected=detected))
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        report.update(status='passed',comparison=comparison,operations_per_arm=12,negative_controls=4)
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
    if report['status']!='passed': raise RuntimeError('Incomplete roundtrip')


if __name__=='__main__': main()
