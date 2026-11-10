#!/usr/bin/env python3
"""One manually bound module deletion, from archived decompiled OrderStatus."""
import difflib
import json
import shutil
import statistics
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest


def specialize(text, adapter):
    """Exact, source-hash-bound region editing, not automatic dependence analysis."""
    start = text.index('   @Override\n   public void run(')
    end = text.index('   private Oorder getOrderDetails', start)
    signature = text[start:text.index('{', start)+1]
    text = text[:start]+signature+'\n      throw new UnsupportedOperationException("Application-adapter profile excludes BenchBase run");\n   }\n\n'+text[end:]
    start = text.index('   private List<String> getOrderLines(')
    end = text.index('   public Customer getCustomerById(', start)
    text = text[:start]+text[end:]
    start = text.index('   public SQLStmt ordStatGetOrderLinesSQL')
    end = text.index('   public SQLStmt payGetCustSQL', start)
    text = text[:start]+text[end:]
    call = '        invoke(p,"getOrderLines",c,1,d,order.o_id,cust);\n'
    if adapter.count(call) != 1 or 'getOrderLines' in text or 'ordStatGetOrderLinesSQL' in text:
        raise ValueError('Source shape changed')
    return text, adapter.replace(call, '')


def admissible(profile):
    return (profile.get('root') == 'application-adapter' and
            profile.get('extra_consumers') == [] and
            profile.get('normal_completion') is True and
            profile.get('read_effects_observable') is False)


def main():
    out = ROOT/'evidence/module-pruning'/('20261104-'+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    container = 'reversedb-pruning-'+out.name
    record = dict(status='running',container=container,commands=[],inputs={},retained_assets={},cleanup_errors=[])
    created = False

    def run(command, log, check=True, stdin=None, timeout=180):
        command = list(map(str, command)); record['commands'].append(command)
        with (out/log).open('w') as stream:
            p = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, stdin=stdin, timeout=timeout)
        if check and p.returncode:
            raise RuntimeError('Command failed; see '+str(out/log))
        return (out/log).read_text()

    def freeze(path):
        relative=path.relative_to(ROOT); target=out/'inputs'/relative
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        record['inputs'][str(relative)]=digest(target)
        return target

    try:
        manifest=json.loads((ARCHIVE/'manifest.json').read_text())
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        bundle=ARCHIVE/'bundle'
        source=bundle/'sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/OrderStatus.java'
        old_adapter=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc/run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
        observation=old_adapter.parents[7]
        # Explicit archive roots, avoiding reliance on working source or implicit recompilation.
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        obs_manifest=json.loads((obs/'manifest.json').read_text())
        if digest(old_adapter)!=obs_manifest[str(old_adapter.relative_to(obs))]:
            raise ValueError('Adapter source changed')
        archived=[source,bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]
        for path in archived:
            if digest(path)!=manifest[str(path.relative_to(ARCHIVE))]: raise ValueError('Archived input changed: '+str(path))
        dependencies=[*sorted(LIB.glob('*.jar')),ROOT/'work/tpcc/runs/c615d3357989/initial.dump']
        for path in dependencies:
            if digest(path)!=retained[str(path)]: raise ValueError('Retained dependency changed')
        protocol=ROOT/'prototype/module_pruning/protocol.json'
        harness=ROOT/'prototype/module_pruning/PruningProbe.java'
        lock=ROOT/'work/paperless/images.lock.json'
        for path in [Path(__file__),ROOT/'scripts/app_db_smoke.py',source,old_adapter,protocol,harness,lock,ARCHIVE/'manifest.json',ARCHIVE/'retained-assets.json',obs/'manifest.json']:
            freeze(path)
        for path in [*archived[1:],*dependencies]: record['retained_assets'][str(path)]=digest(path)
        normal=dict(root='application-adapter',extra_consumers=[],normal_completion=True,read_effects_observable=False)
        profiles=[('declared-profile',normal,True),('display-consumer',{**normal,'extra_consumers':['terminal-display']},False),('query-faults',{**normal,'normal_completion':False},False),('query-observer',{**normal,'read_effects_observable':True},False),('unknown-context',{},False)]
        decisions=[]
        for name,profile,expected in profiles:
            decision=admissible(profile)
            assert decision==expected
            decisions.append(dict(name=name,profile=profile,admitted=decision))
        record['admission_checks']=decisions
        original=source.read_text(); adapter=old_adapter.read_text()
        pruned,pruned_adapter=specialize(original,adapter)
        generated=out/'generated'; generated.mkdir()
        (generated/'OrderStatus.java').write_text(pruned)
        (generated/'TpccApp.java').write_text(pruned_adapter)
        diff=''.join(difflib.unified_diff(original.splitlines(True),pruned.splitlines(True),fromfile='original/OrderStatus.java',tofile='pruned/OrderStatus.java'))
        diff+=''.join(difflib.unified_diff(adapter.splitlines(True),pruned_adapter.splitlines(True),fromfile='original/TpccApp.java',tofile='pruned/TpccApp.java'))
        (out/'deletion.diff').write_text(diff)
        classes=out/'pruned-classes'; classes.mkdir()
        probe=out/'probe-classes'; probe.mkdir()
        empty=out/'empty-sourcepath'; empty.mkdir()
        libs=':'.join(map(str,sorted(LIB.glob('*.jar'))))
        support=str(bundle/'candidate-classes')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',str(bundle/'reference.jar')+':'+support+':'+libs,'-d',classes,generated/'OrderStatus.java',generated/'TpccApp.java'],'compile-pruned.log')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',support+':'+libs,'-d',probe,out/'inputs'/harness.relative_to(ROOT)],'compile-probe.log')
        member='com/oltpbenchmark/benchmarks/tpcc/procedures/OrderStatus.class'
        with zipfile.ZipFile(bundle/'reference.jar') as original_jar,zipfile.ZipFile(out/'pruned.jar','w',compression=zipfile.ZIP_DEFLATED) as reduced:
            assert len(original_jar.namelist())==len(set(original_jar.namelist()))
            for item in original_jar.infolist():
                reduced.writestr(item,(classes/member).read_bytes() if item.filename==member else original_jar.read(item))
        # The modified procedure must load from pruned.jar, never the loose compilation directory.
        (classes/member).unlink()
        record['source_binding']='manual exact-region transform of hash-verified decompiler output; adapter source retained explicitly'
        record['scope']='Only application adapter entry; BenchBase run replaced with explicit rejection; other source methods unchanged'
        (out/'log4j.properties').write_text('log4j.rootLogger=OFF\n')
        image=json.loads(lock.read_text())['images']['postgres']
        if run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log').strip()!=image['id']: raise ValueError('Pinned image mismatch')
        record['image']=image
        run(DOCKER+['run','-d','--pull=never','--name',container,'--label','reversedb.experiment=module-pruning','--memory=1536m','--cpus=1','--tmpfs','/var/lib/postgresql:rw,size=1024m','-p','127.0.0.1::5432','-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',image['resolved'],'-c','timezone=UTC'],'start.log')
        created=True
        for i in range(40):
            ready=run(DOCKER+['exec',container,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False,timeout=5)
            if 'accepting connections' in ready: break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        address=run(DOCKER+['port',container,'5432/tcp'],'port.log').strip()
        if not address.startswith('127.0.0.1:') or '\n' in address: raise ValueError('Unexpected port')
        print('Restoring frozen database for one-module experiment',flush=True)
        with dependencies[-1].open('rb') as dump:
            run(DOCKER+['exec','-i',container,'pg_restore','--exit-on-error','-U','tpcc','-d','tpcc'],'restore.log',stdin=dump)
        url='jdbc:postgresql://'+address+'/tpcc?sslmode=disable&gssEncMode=disable&prepareThreshold=0'
        run([JDK/'java','-Xms64m','-Xmx256m','-XX:+UseSerialGC','-Duser.timezone=UTC','-Dlog4j.configuration='+ (out/'log4j.properties').as_uri(),'-cp',str(probe)+':'+support+':'+libs,'research.PruningProbe',url,bundle/'reference-adapter',bundle/'reference.jar',classes,out/'pruned.jar',out/'results.json',out/'inputs'/protocol.relative_to(ROOT)],'probe.log',timeout=180)
        results=json.loads((out/'results.json').read_text())
        assert results['status']=='passed' and len(results['samples'])==180 and len(results['checks'])==8
        summaries=[]
        for case in json.loads(protocol.read_text())['performance_cases']:
            medians={arm:statistics.median(s['ms'] for s in results['samples'] if s['case']==case and s['arm']==arm) for arm in ['original','pruned']}
            summaries.append(dict(case=case,median_ms=medians,reduction_percent=100*(1-medians['pruned']/medians['original'])))
        record.update(status='passed',semantic_cases=8,timed_samples=180,summary=summaries)
        for path,expected in record['retained_assets'].items():
            if digest(Path(path))!=expected: raise ValueError('Retained dependency changed during experiment')
    except BaseException as error:
        record.update(status='failed',error=repr(error)); raise
    finally:
        if created:
            try:
                run(DOCKER+['logs',container],'postgres.log',check=False)
                run(DOCKER+['rm','-f','-v',container],'cleanup.log')
            except Exception as error: record['cleanup_errors'].append(repr(error))
        if record['cleanup_errors']: record['status']='failed'
        (out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
        (out/'manifest.json').write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
        print(json.dumps(dict(status=record['status'],output=str(out),cleanup_errors=record['cleanup_errors'])),flush=True)
    if record['status']!='passed': raise RuntimeError('Experiment failed')


if __name__=='__main__':
    main()
