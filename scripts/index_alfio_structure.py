#!/usr/bin/env python3
"""Frozen offline whole-application index plus an explicitly open business envelope."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile
from graphlib import TopologicalSorter
from alfio_baseline import ROOT, WORK, JDK, JAR, SHA, digest
from bytecode_structure import index_classes, analyze
from trace_alfio_dependencies import tokens

PREVIOUS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'
ROOT_METHODS=[('alfio/manager/TicketReservationManager',n) for n in
              ['createTicketReservation','orderSummaryForReservationId','cancelPendingReservation']]+[
              ('alfio/manager/WaitingQueueSubscriptionProcessor','handleWaitingTickets')]

FIXTURE='''package fixture;
import java.lang.annotation.*;
@Retention(RetentionPolicy.RUNTIME) @interface Marker { String value(); }
interface Task { int run(); }
class Parent { public int run() { return 7; } }
class Child extends Parent implements Task { }
class Other implements Task { public int run() { return 9; } }
class Outside { public int untouched() { return 123; } }
public class Example {
  static int saved;
  @Marker("retain SQL :binding exactly")
  public int root(Task task, int value) throws Exception {
    Runnable callback=()->helper(); callback.run();
    Class.forName("fixture.Outside"); saved=task.run();
    try { switch(value) { case 0:return a(); case 9:return saved; default:throw new IllegalArgumentException(); } }
    catch(IllegalArgumentException ex) { return -1; }
  }
  void helper() { saved++; }
  int a() { return b(); } int b() { return a(); }
}
'''


def main():
    out=ROOT/'evidence/alfio-structure'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],checks=[],database_runs=0,deleted_modules=0)
    def save(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,condition):
        assert condition,name
        report['checks'].append(name)
    def run(command,log):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
        (out/log).write_bytes(p.stdout)
        if p.returncode: raise RuntimeError(str(out/log))
    def scan(config,name,cp,toolclasses):
        save(name+'-classpath.json',config)
        run([JDK/'java','-Xmx1024m','-cp',str(toolclasses)+os.pathsep+cp,'research.alfio.BytecodeStructureIndex',
             out/(name+'-classpath.json'),out/(name+'.jsonl.gz')],name+'.log')
        with gzip.open(out/(name+'.jsonl.gz'),'rt') as f:return index_classes(json.loads(line) for line in f)
    try:
        check('official artifact SHA',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        for name in ['scripts/index_alfio_structure.py','scripts/bytecode_structure.py','scripts/alfio_baseline.py',
                     'scripts/trace_alfio_dependencies.py','prototype/alfio/BytecodeStructureIndex.java']:
            src=ROOT/name;dst=out/'inputs'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);report['inputs'][name]=digest(src)
        previous_manifest=json.loads((PREVIOUS/'manifest.json').read_text())
        for name in ['bytecode-facts.json','dependencies.json']:
            p=PREVIOUS/name;check('frozen '+name,digest(p)==previous_manifest[name]);report['retained_assets'][str(p)]=digest(p)
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            p=ROOT/'scripts'/name
            old=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts'/name
            check('original optimizer unchanged '+name,digest(p)==digest(old));report['retained_assets'][str(p)]=digest(p)
        binary=WORK/'official-binary';application=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            expected_app={n.removeprefix('BOOT-INF/classes/'):hashlib.sha256(jar.read(n)).hexdigest()
                          for n in jar.namelist() if n.startswith('BOOT-INF/classes/') and n.endswith('.class')}
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary/member)==hashlib.sha256(jar.read(member)).hexdigest(),member
            check('no extra application bytecode',set(expected_app)=={str(p.relative_to(application)) for p in application.rglob('*.class')})
        cp=os.pathsep.join(map(str,libs));toolclasses=out/'tool-classes';toolclasses.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',toolclasses,
             ROOT/'prototype/alfio/BytecodeStructureIndex.java'],'compile.log')
        fixture=out/'fixture';fixture.mkdir();(fixture/'Example.java').write_text(FIXTURE)
        fixtureclasses=out/'fixture-classes';fixtureclasses.mkdir()
        run([JDK/'javac','--release','17','-g','-d',fixtureclasses,fixture/'Example.java'],'fixture-compile.log')
        fc,dups=scan([dict(path=str(fixtureclasses),scope='application')],'fixture-index',cp,toolclasses)
        fm={m['id']:m for c in fc.values() for m in c['methods']}
        root='fixture/Example#root(Lfixture/Task;I)I';fg=analyze(fc,[root]);save('fixture-graph.json',fg)
        reached=set(fg['methods_in_structural_envelope'])
        check('inherited interface implementation included','fixture/Parent#run()I' in reached)
        check('alternative interface implementation included','fixture/Other#run()I' in reached)
        check('lambda callback expanded','fixture/Example#helper()V' in reached)
        check('reflection remains an unknown boundary',any(b['kind']=='reflection_or_runtime_linkage' for b in fg['boundaries']))
        check('unreached class never becomes deletable','fixture/Outside#untouched()I' not in reached and not fg['policy']['deletion_authorized'])
        check('recursive methods condensed together',any(set(g)=={'fixture/Example#a()I','fixture/Example#b()I'} for g in fg['condensation']['components']))
        # javac splits this single catch into three protected ranges around returns.
        check('switch and exception handler preserved',any(b['opcode'] in [170,171] for b in fm[root]['branches'])
              and len(fm[root]['handlers'])==3 and len({h['handler'] for h in fm[root]['handlers']})==1
              and all(h['type']=='java/lang/IllegalArgumentException' for h in fm[root]['handlers']))
        check('annotation value preserved',fm[root]['annotations'][0]['annotation']['values']['value']=='retain SQL :binding exactly')
        header,_=scan([dict(path=str(fixtureclasses),scope='dependency')],'fixture-header-index',cp,toolclasses)
        check('dependency headers do not invent bodies',all(not m['body_indexed'] and 'calls' not in m for c in header.values() for m in c['methods']))
        chosen,duplicates=index_classes([fc['fixture/Example'],dict(fc['fixture/Example'],source='shadow')])
        check('classpath shadowing is explicit',chosen['fixture/Example']['source']!='shadow' and len(duplicates)==1)
        config=[dict(path=str(application),scope='application')]+[dict(path=str(p),scope='dependency') for p in libs]
        classes,duplicates=scan(config,'structure-index',cp,toolclasses)
        app={n:c for n,c in classes.items() if c['application']}
        check('all official application class bytes indexed',{c['entry']:c['sha256'] for c in app.values()}==expected_app)
        check('all runtime library containers visited',len(config)==158)
        # Derive effective Java 17 class counts independently from ZIP directory/manifest.
        expected_counts={}
        for lib in libs:
            with zipfile.ZipFile(lib) as z:
                manifest=z.read('META-INF/MANIFEST.MF').decode(errors='replace') if 'META-INF/MANIFEST.MF' in z.namelist() else ''
                multi='multi-release: true' in manifest.lower(); entries=set()
                for n in z.namelist():
                    if not n.endswith('.class'):continue
                    if n.startswith('META-INF/versions/'):
                        parts=n.split('/',3)
                        if multi and parts[2].isdigit() and 9<=int(parts[2])<=17:entries.add(parts[3])
                    elif not n.startswith('META-INF/'):entries.add(n)
                expected_counts[str(lib)]=len(entries)
        actual_counts={str(p):0 for p in libs}
        with gzip.open(out/'structure-index.jsonl.gz','rt') as stream:
            for line in stream:
                row=json.loads(line)
                if not row['application']: actual_counts[row['source']]+=1
        check('every effective Java17 dependency class counted',actual_counts==expected_counts)
        save('coverage.json',dict(application_classes=len(app),dependency_classes_by_archive=actual_counts,duplicates=duplicates,
                                  classpath_policy='application first then official classpath.idx; multi-release Java17 view',
                                  excluded='JDK module bodies, boot launcher, non-classpath tools, runtime-generated and external classes'))
        roots=[]
        for owner,name in ROOT_METHODS:
            found=[m['id'] for m in app[owner]['methods'] if m['id'].startswith(owner+'#'+name+'(')]
            check('root found '+name,bool(found));roots.extend(sorted(found))
        graph=analyze(classes,roots);save('business-graph.json',graph)
        dag=graph['condensation'];sorter=TopologicalSorter({i:set() for i in range(len(dag['components']))})
        for a,b in dag['edges']:sorter.add(b,a)
        check('condensation is acyclic',len(list(sorter.static_order()))==len(dag['components']))
        old=json.loads((PREVIOUS/'bytecode-facts.json').read_text());methods={m['id']:m for c in app.values() for m in c['methods']}
        for c in old['classes']:
            assert app[c['class']]['sha256']==c['sha256']
            for m in c['methods']:
                now=methods[m['id']]
                assert m['annotations']==[a['annotation'] for a in now['annotations'] if a['visibility']=='runtime']
                assert [x['target'] for x in m['calls'] if x['kind']=='direct_call']==[x['target'] for x in now['calls'] if x['kind']=='invoke']
        check('all previously indexed calls and annotations retained',True)
        catalog=[]
        for mid,m in methods.items():
            for a in m['annotations']:
                a=a['annotation']
                if a['type']=='Lch/digitalfondue/npjt/Query;' and isinstance(a['values'].get('value'),str):
                    catalog.append(dict(method=mid,sql=a['values']['value']))
        lookup={}
        for item in catalog:lookup.setdefault(tokens(item['sql']),[]).append(item['method'])
        witness=json.loads((PREVIOUS/'dependencies.json').read_text());matches=[]
        for statement in witness['observed_statements']:
            candidates=lookup.get(tokens(statement['sql']),[])
            assert set(statement['annotation_matches']).issubset(candidates)
            matches.append(dict(id=statement['id'],operation=statement['operation'],sql=statement['sql'],
                                matching_declarations=candidates,authority='SQL text matching hint only; no dynamic Java dispatch proof'))
        save('sql-witness-links.json',dict(catalog=catalog,observations=matches))
        check('all frozen SQL witness matches retained',len(matches)==len(witness['observed_statements']))
        report.update(status='passed',summary=graph['summary'],runtime_jars=len(libs),
                      indexed_dependency_class_entries=sum(actual_counts.values()),shadowed_class_entries=len(duplicates),
                      sql_annotations=len(catalog),frozen_observations=len(matches),roots=roots,
                      scope='all application bodies; bundled dependency signatures only; no deletability or performance proof')
        for p,sha in report['retained_assets'].items():assert digest(Path(p))==sha
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()};save('manifest.json',manifest)
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),summary=report.get('summary')),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
