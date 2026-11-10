#!/usr/bin/env python3
"""Read-only focused decompilation of the indexed repository-to-JDBC boundary."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile
from alfio_baseline import ROOT, WORK, JDK, JAR, SHA, digest

INDEX=ROOT/'evidence/alfio-structure/20261106-e2dc533105d2'
TARGETS=['ch/digitalfondue/npjt/QueryFactory','ch/digitalfondue/npjt/QueryType',
         'ch/digitalfondue/npjt/ConstructorAnnotationRowMapper',
         'org/springframework/jdbc/core/namedparam/NamedParameterJdbcTemplate']


def main():
    out=ROOT/'evidence/alfio-jdbc-boundary'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],targets={},database_runs=0,modified_runtime_classes=0,
                scope='source reading and javap corroboration only; no recompilation, deployment, semantic admission or speedup claim')
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def run(command,log):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
        (out/log).write_bytes(p.stdout)
        if p.returncode:raise RuntimeError(str(out/log))
    try:
        assert digest(JAR)==SHA;report['retained_assets'][str(JAR)]=SHA
        index=INDEX/'structure-index.jsonl.gz';assert digest(index)==json.loads((INDEX/'manifest.json').read_text())[index.name]
        report['retained_assets'][str(index)]=digest(index)
        tool=ROOT/'work/tpcc/tools/vineflower-1.12.0/vineflower-1.12.0.jar'
        assert digest(tool)=='1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d'
        report['retained_assets'][str(tool)]=digest(tool)
        for p in [Path(__file__).resolve(),ROOT/'scripts/alfio_baseline.py']:
            name=str(p.relative_to(ROOT));dest=out/'inputs'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);report['inputs'][name]=digest(p)
        selected={}
        with gzip.open(index,'rt') as f:
            for line in f:
                c=json.loads(line)
                if c['class'].split('$')[0] in TARGETS:selected[c['class']]=c
        target=out/'bytecode-targets';target.mkdir();sources=out/'decompiled';sources.mkdir()
        with zipfile.ZipFile(JAR) as jar:
            libs=[WORK/'official-binary'/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for lib in libs:
                assert digest(lib)==hashlib.sha256(jar.read('BOOT-INF/lib/'+lib.name)).hexdigest()
                report['retained_assets'][str(lib)]=digest(lib)
        for name,c in selected.items():
            with zipfile.ZipFile(c['source']) as jar: data=jar.read(c['entry'])
            assert hashlib.sha256(data).hexdigest()==c['sha256']
            dest=target/(name+'.class');dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        run([JDK/'java','-Xmx1024m','-XX:ActiveProcessorCount=2','-jar',tool,'--thread-count=2','--folder',
             '--bytecode-source-mapping=true','--skip-extra-files=true',*['-e='+str(p) for p in libs],target,sources],'decompile.log')
        cp=os.pathsep.join(map(str,libs))
        for name in TARGETS:
            source=sources/(name+'.java');assert source.is_file(),name
            text=source.read_text();assert "Couldn't be decompiled" not in text and '$VF: Could not' not in text,name
            run([JDK/'javap','-p','-c','-s','-classpath',cp,name.replace('/','.')],Path(name).name+'-javap.log')
            report['targets'][name]=dict(source=str(source.relative_to(out)),source_sha256=digest(source),
                                         original_class_sha256=selected[name]['sha256'],library=selected[name]['source'])
        report.update(status='passed',target_class_files=len(selected),source_files=len(list(sources.rglob('*.java'))))
        for p,sha in report['retained_assets'].items():assert digest(Path(p))==sha
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),targets=len(report['targets']))),flush=True)


if __name__=='__main__':main()
