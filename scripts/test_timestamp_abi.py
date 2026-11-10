#!/usr/bin/env python3
"""Fixed timestamp regression on the full frozen database, no performance run."""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from generate_neworder_fusion import emit, adapter_variant, HELPER
from generate_payment_segments import generate


def main():
    out=ROOT/'evidence/timestamp-abi'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    name='reversedb-timestamp-'+out.name
    report=dict(status='running',commands=[],inputs={},retained_assets={},cleanup_errors=[])
    resources=[]
    env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only')
    def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(command,log,stdin=None,check=True,timeout=180):
        command=list(map(str,command)); report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Command failed: '+str(out/log))
        return p.stdout.decode()
    def freeze(path):
        target=out/'inputs'/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        report['inputs'][str(path.relative_to(ROOT))]=digest(target)
    def sql(query,log):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d','experiment','-XAt','-v','ON_ERROR_STOP=1','-c',query],log).strip()
    try:
        bundle=ARCHIVE/'bundle'; n=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'
        p=ROOT/'evidence/payment-segments/20261105-707221611457'
        t=ROOT/'evidence/payment-fusion-transfer/20261105-735301a5f9db'
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=obs/'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        source=bundle/'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/Payment.java'
        verified={ARCHIVE:[bundle/'reference.jar',source,*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))],
          n:[n/'transaction-ir.json',n/'generated/full.sql',n/'generated/FusedNewOrder.java',n/'generated/TpccApp.java'],
          p:[p/'generated/install.sql',p/'generated/SegmentedPayment.java',p/'generated/TpccApp.java'],
          t:[t/'extracted.json'],obs:[adapter]}
        for archive,paths in verified.items():
            manifest=json.loads((archive/'manifest.json').read_text())
            for path in paths:
                assert digest(path)==manifest[str(path.relative_to(archive))]
                report['retained_assets'][str(path)]=digest(path)
        dump=ROOT/'work/tpcc/runs/c615d3357989/initial.dump'
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for path in [dump,*sorted(LIB.glob('*.jar'))]:
            assert digest(path)==retained[str(path)]; report['retained_assets'][str(path)]=digest(path)
        lock=ROOT/'work/paperless/images.lock.json'
        inputs=[Path(__file__).resolve(),lock,adapter,source,n/'transaction-ir.json',t/'extracted.json',
          ROOT/'prototype/module_pruning/FusionProbe.java',ROOT/'prototype/module_pruning/TimestampAbiProbe.java',
          *[ROOT/'scripts'/f for f in ('generate_neworder_fusion.py','generate_payment_segments.py','fusion_regions.py','fusion_placement.py','fusion_placement_contracts.py','payment_fusion_transfer.py','app_db_smoke.py')]]
        for path in inputs: freeze(path)
        save(out/'protocol.json',dict(arms=['original','fixed','historical'],cases=['NewOrder','PaymentGC','PaymentBC'],
          clocks=[1762300800123,9223372036825200000],expected_successes=15,expected_historical_errors=3,expected_invalid_item_aborts=2,
          comparisons=['complete responses','full nine-table state before rollback','full nine-table restoration after every execution','actual stored timestamp','unchanged fused execute count'],
          profile='frozen PostgreSQL schema and complete initial data; serial SERIALIZABLE; UTC; WAL/fsync/synchronous_commit retained',
          stop='20 fixed helper executions; no timing or expanded workload',scope='reference helper via frozen clock mapping; repaired candidates passed through unchanged; abort mapping checked against frozen TpccApp.main'))
        generated=out/'generated'; generated.mkdir()
        nsql,_=emit(json.loads((n/'transaction-ir.json').read_text()),'full')
        payment=generate(source.read_text(),adapter.read_text(),json.loads((t/'extracted.json').read_text()))
        assert nsql==(n/'generated/full.sql').read_text()
        assert payment['sql']==(p/'generated/install.sql').read_text()
        (generated/'install.sql').write_text('CREATE SCHEMA reversedb_fusion;\n'+nsql+'\n'+payment['sql'])
        libs=':'.join(map(str,sorted(LIB.glob('*.jar')))); support=str(bundle/'candidate-classes')
        cp=str(bundle/'reference.jar')+':'+support+':'+libs
        adapters={}
        for label,helper,bridge in [('new-fixed',HELPER,adapter_variant(adapter.read_text())),
          ('payment-fixed',payment['helper'],payment['adapter']),
          ('new-old',(n/'generated/FusedNewOrder.java').read_text(),(n/'generated/TpccApp.java').read_text()),
          ('payment-old',(p/'generated/SegmentedPayment.java').read_text(),(p/'generated/TpccApp.java').read_text())]:
            folder=generated/label; folder.mkdir(); classes=folder/'classes'; classes.mkdir(); empty=folder/'empty'; empty.mkdir()
            helper_path=folder/('FusedNewOrder.java' if label.startswith('new-') else 'SegmentedPayment.java')
            helper_path.write_text(helper); (folder/'TpccApp.java').write_text(bridge)
            run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,folder/'TpccApp.java',helper_path],label+'-compile.log')
            adapters[label]=classes
        probes=out/'probe-classes'; probes.mkdir()
        run([JDK/'javac','--release','23','-cp',support+':'+libs,'-d',probes,ROOT/'prototype/module_pruning/FusionProbe.java',ROOT/'prototype/module_pruning/TimestampAbiProbe.java'],'probe-compile.log')
        image=json.loads(lock.read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()==image['id']
        report['image']=image
        run(DOCKER+['volume','create','--label','reversedb.experiment=timestamp-abi',name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=timestamp-abi','--memory=1536m','--cpus=2',
          '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_USER=tpcc',
          '-e','POSTGRES_PASSWORD=synthetic-tpcc-only','-e','POSTGRES_DB=experiment',image['resolved'],
          '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log').strip(); assert port.startswith('127.0.0.1:') and '\n' not in port
        with dump.open('rb') as stream: run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],'restore.log',stdin=stream)
        gate=json.loads(sql("SELECT json_build_object('fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'),'timezone',current_setting('timezone'))",'gate.log'))
        assert all(gate[k]=='on' for k in ('fsync','synchronous_commit','full_page_writes')) and gate['timezone']=='UTC'
        sql((generated/'install.sql').read_text(),'install.log')
        print('Compiled all arms and restored the frozen database; running 20 bounded executions',flush=True)
        run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2','-cp',str(probes)+':'+support+':'+libs,
          'research.TimestampAbiProbe',f'jdbc:postgresql://{port}/experiment?sslmode=disable&reWriteBatchedInserts=true',bundle/'reference-adapter',
          adapters['new-fixed'],adapters['payment-fixed'],adapters['new-old'],adapters['payment-old'],bundle/'reference.jar',out/'probe-results.json'],'probe.log',timeout=300)
        result=json.loads((out/'probe-results.json').read_text())
        assert result['status']=='passed' and len(result['checks'])==15 and len(result['negative_controls'])==3 and len(result['aborts'])==2
        for path,sha in report['retained_assets'].items(): assert digest(Path(path))==sha
        report.update(status='passed',successful_executions=15,historical_failures=3,business_aborts=2,no_timing=True,sql_unchanged=True)
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container':
                    run(DOCKER+['logs',name],'postgres.log',check=False)
                    run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save(out/'run.json',report)
        save(out/'manifest.json',{str(f.relative_to(out)):digest(f) for f in sorted(out.rglob('*')) if f.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)
    if report['status']!='passed': raise RuntimeError('Incomplete timestamp regression')


if __name__=='__main__': main()
