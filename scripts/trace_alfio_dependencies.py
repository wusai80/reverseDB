#!/usr/bin/env python3
"""One unchanged replay, PostgreSQL statement witness and binary dependency facts."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
from alfio_baseline import ROOT, WORK, JDK, DOCKER, JAR, SHA, digest
from test_alfio_roundtrip import compare_folders

BASE=ROOT/'evidence/alfio-roundtrip/20261106-17b16f1a21eb'


def tokens(sql):
    # Matching hints only. Preserve literals; named/numbered binds share a token.
    parts=re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|::|:[A-Za-z_]\w*|\$\d+|[A-Za-z_]\w*|\d+|[^\s]",sql)
    result=[]
    for token in parts:
        if token.startswith(('"',"'")): result.append(token)
        elif token.startswith(':') and token!='::' or token.startswith('$') or token=='?': result.append('?')
        else: result.append(token.lower())
    text=' '.join(result)
    return re.sub(r'\( \?(?: , \?)* \)', '( ?list )',text)


def analyze(facts,records):
    methods={m['id']:m for c in facts['classes'] for m in c['methods']}; catalog=[]
    for c in facts['classes']:
        for m in c['methods']:
            for annotation in m['annotations']:
                if annotation['type']=='Lch/digitalfondue/npjt/Query;' and isinstance(annotation['values'].get('value'),str):
                    catalog.append(dict(method=m['id'],sql=annotation['values']['value'],parameters=m['parameters'],normalized=tokens(annotation['values']['value'])))
    lookup={}
    for item in catalog: lookup.setdefault(item['normalized'],[]).append(item['method'])
    operation=None; sql=[]; marker_count=0
    for record in records:
        message=record.get('message','')
        marker=re.search(r"SELECT 'rdbstep:([a-z0-9-]+)'",message)
        if marker: operation=marker[1]; marker_count+=1; continue
        match=re.match(r'(?:execute [^:]+|statement): (.*)',message,re.S)
        if operation is None or operation=='finished' or not match or record.get('application_name')=='rdb-observer': continue
        query=match[1]
        sql.append(dict(id='sql'+str(len(sql)),operation=operation,pid=record.get('pid'),sql=query,parameters=record.get('detail'),
                        annotation_matches=lookup.get(tokens(query),[])))
    root=next(k for k in methods if k.startswith('alfio/manager/TicketReservationManager#createTicketReservation('))
    visited=set(); pending=[root]; edges=[]
    while pending:
        current=pending.pop()
        if current in visited: continue
        visited.add(current)
        for call in methods[current]['calls']:
            edge=dict(caller=current,**call); edges.append(edge)
            # Expand only same-manager methods; all other module bodies remain explicit boundaries.
            if call['target'].startswith('alfio/manager/TicketReservationManager#') and call['target'] in methods: pending.append(call['target'])
    dynamic_edges=[dict(before=a['id'],after=b['id'],kind='observed_statement_order',operation=a['operation']) for a,b in zip(sql,sql[1:]) if a['operation']==b['operation']]
    summary={}
    for row in sql: summary[row['operation']]=summary.get(row['operation'],0)+1
    return dict(root=root,sql_catalog=catalog,reachable_manager_methods=sorted(visited),static_call_edges=edges,
                observed_statements=sql,execution_order_edges=dynamic_edges,statements_per_operation=summary,marker_count=marker_count,
                caveats=['statement order is not a proven data/control dependence','server executions are not JDBC roundtrip counts','SQL annotation matches may be ambiguous and are matching hints, not proxy call observations','static branches retained as instruction targets; no predicate or alias proof'])


def main():
    out=ROOT/'evidence/alfio-dependencies'/('20261106-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    container_name='reversedb-alfio-deps-'+out.name; resources=[]
    report=dict(status='running',inputs={},retained_assets={},commands=[],cleanup_errors=[])
    env=dict(os.environ,ALFIO_DB_PASSWORD='synthetic-alfio-only')
    def save(file,value): (out/file).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(command,log,stdin=None,check=True):
        command=list(map(str,command)); report['commands'].append(command)
        result=subprocess.run(command,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
        (out/log).write_bytes(result.stdout)
        if check and result.returncode: raise RuntimeError('Failed: '+str(out/log))
        return result.stdout.decode(errors='replace').strip()
    def sql(query,log,database='alfio'):
        return run(DOCKER+['exec',container_name,'psql','-U','postgres','-d',database,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)
    def retain(file):
        p=BASE/file; assert digest(p)==json.loads((BASE/'manifest.json').read_text())[file]; report['retained_assets'][str(p)]=digest(p); return p
    def freeze(p):
        target=out/'inputs'/p.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target); report['inputs'][str(p.relative_to(ROOT))]=digest(target)
    try:
        assert digest(JAR)==SHA; report['retained_assets'][str(JAR)]=SHA
        source=retain('generated/AlfioRoundtripProbe.java'); agent=retain('entropy-agent.jar'); fixture=retain('fixture.sql')
        for file in sorted((BASE/'original').glob('*.json')): retain('original/'+file.name)
        for p in [Path(__file__).resolve(),ROOT/'scripts/alfio_baseline.py',ROOT/'scripts/test_alfio_roundtrip.py',ROOT/'prototype/alfio/AlfioDependencyExtract.java',ROOT/'work/paperless/images.lock.json']: freeze(p)
        binary=WORK/'official-binary'; original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary/member)==__import__('hashlib').sha256(jar.read(member)).hexdigest()
        cp=os.pathsep.join(map(str,[original,*libs,agent])); generated=out/'generated'; generated.mkdir(); classes=out/'classes'; classes.mkdir(); empty=out/'empty'; empty.mkdir()
        text=source.read_text()
        old='      require(ClockProvider.clock().instant().equals(NOW),'; assert text.count(old)==1
        text=text.replace(old,'      observer.setClientInfo("ApplicationName","rdb-observer");\n'+old)
        text,count=re.subn(r'      ReplayEntropy.begin\("([a-z0-9-]+)"\);',r'      mark(observer,"\1");\n      ReplayEntropy.begin("\1");',text); assert count==12
        old='      save("entropy.json",ReplayEntropy.finish());'; assert text.count(old)==1
        text=text.replace(old,'      mark(observer,"finished");\n'+old)
        old='  public static void main(String[] args) throws Exception {'; assert text.count(old)==1
        text=text.replace(old,'''  static void mark(Connection connection,String label) throws Exception {
    if(!label.matches("[a-z0-9-]+")) throw new IllegalArgumentException(label);
    try(Statement statement=connection.createStatement()) { statement.execute("SELECT 'rdbstep:"+label+"'"); }
  }
'''+old)
        probe=generated/'AlfioRoundtripProbe.java'; probe.write_text(text)
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe,ROOT/'prototype/alfio/AlfioDependencyExtract.java'],'compile.log')
        run([JDK/'java','-cp',str(classes)+os.pathsep+cp,'research.alfio.AlfioDependencyExtract',original,out/'bytecode-facts.json'],'extract.log')
        save('protocol.json',dict(scope='one unchanged original replay; binary facts and database statement order; no optimization',
          fixture='identical full dump from successful roundtrip',source_control='same archived entropy agent',
          observation='PostgreSQL JSON statement log, observer connection excluded; operation markers on separate observer connection',
          preservation='exact comparison with previous original replay across the same 53 files',
          stop='one replay; propose one candidate only; unknown effects remain barriers'))
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']; report['image']=image
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id']
        run(DOCKER+['volume','create','--label','reversedb.experiment=alfio-dependencies',container_name],'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',container_name,'--label','reversedb.experiment=alfio-dependencies','--memory=1536m','--cpus=2',
          '--mount','type=volume,src='+container_name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-admin-only',image['resolved'],
          '-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on',
          '-c','logging_collector=on','-c','log_destination=jsonlog','-c','log_directory=/tmp/rdb-logs','-c','log_filename=statements.log','-c','log_statement=all','-c','log_parameter_max_length=-1'],'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',container_name,'pg_isready','-U','postgres'],f'ready-{i}.log',check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'",'role.log','postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user','database.log','postgres')
        with fixture.open('rb') as stream: run(DOCKER+['exec','-i',container_name,'psql','-U','alfio_user','-d','alfio','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        port=run(DOCKER+['port',container_name,'5432/tcp'],'port.log'); assert port.startswith('127.0.0.1:') and '\n' not in port
        replay=out/'replay'; replay.mkdir()
        run([JDK/'java','-Xmx1024m','-XX:ActiveProcessorCount=2','-Duser.timezone=UTC','-javaagent:'+str(agent),
             '-cp',str(classes)+os.pathsep+cp,'research.alfio.AlfioRoundtripProbe','jdbc:postgresql://'+port+'/alfio?sslmode=disable',replay,'replay'],'replay.log')
        comparison=compare_folders(BASE/'original',replay); save('comparison.json',comparison); assert comparison['equal'],comparison
        logfile=sql("SELECT pg_current_logfile('jsonlog')",'log-location.log'); assert logfile=='/tmp/rdb-logs/statements.json',logfile
        raw=run(DOCKER+['exec',container_name,'cat',logfile],'postgres-statements.jsonl')
        records=[json.loads(line) for line in raw.splitlines() if line]
        result=analyze(json.loads((out/'bytecode-facts.json').read_text()),records); save('dependencies.json',result)
        assert result['marker_count']==13 and len(result['statements_per_operation'])==12
        assert all(any(s['sql'].upper()=='COMMIT' for s in result['observed_statements'] if s['operation']==op) for op in result['statements_per_operation'] if op!='03-insufficient-stock')
        assert any(s['sql'].upper()=='ROLLBACK' and s['operation']=='03-insufficient-stock' for s in result['observed_statements'])
        for p,sha in report['retained_assets'].items(): assert digest(Path(p))==sha
        report.update(status='passed',compared_files=comparison['compared_files'],statement_counts=result['statements_per_operation'],sql_catalog_entries=len(result['sql_catalog']),reachable_manager_methods=len(result['reachable_manager_methods']))
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container': run(DOCKER+['logs',container_name],'postgres.log',check=False); run(DOCKER+['rm','-f','-v',container_name],'cleanup-container.log')
                else: run(DOCKER+['volume','rm',container_name],'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status']='failed'
        save('run.json',report); manifest={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()}; save('manifest.json',manifest)
        assert all(digest(out/p)==sha for p,sha in manifest.items())
        print(json.dumps(dict(status=report['status'],output=str(out),files=len(manifest))),flush=True)
    if report['status']!='passed': raise RuntimeError('Dependency observation incomplete')


if __name__=='__main__': main()
