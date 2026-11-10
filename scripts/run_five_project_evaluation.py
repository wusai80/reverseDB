"""Execute a frozen five-subject replication schedule, sequentially, once."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    out=Path(sys.argv[1]).resolve()
    protocol=json.loads((out/'protocol.json').read_text())
    for path,digest in protocol['frozen_sources'].items():assert sha(ROOT/path)==digest,path
    report=dict(status='running',started_utc=datetime.now(timezone.utc).isoformat(),projects=[])
    save(out/'run.json',report)
    for spec in protocol['schedule']:
        parent=ROOT/spec['evidence_parent'];before=set(parent.iterdir())
        env=dict(os.environ,**spec.get('environment',{}))
        item=dict(project=spec['project'],started_utc=datetime.now(timezone.utc).isoformat(),classification=spec['classification'])
        report['projects'].append(item);save(out/'run.json',report)
        print('START',spec['project'],flush=True)
        with (out/(spec['project']+'.log')).open('w') as log:
            result=subprocess.run(spec['command'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        created=sorted(p for p in set(parent.iterdir())-before if p.is_dir())
        item.update(exit_code=result.returncode,completed_utc=datetime.now(timezone.utc).isoformat(),
                    evidence=[str(p.relative_to(ROOT)) for p in created])
        item['status']='passed' if result.returncode==0 and len(created)==1 else 'failed'
        if len(created)==1:
            for name in ['run.json','report.json','summary.json']:
                p=created[0]/name
                if p.exists():
                    record=json.loads(p.read_text())
                    if record.get('status')=='failed' or record.get('cleanup_errors'):item['status']='failed'
            if (created[0]/'failure.json').exists():item['status']='failed'
        save(out/'run.json',report)
        print('END',spec['project'],item['status'],item['evidence'],flush=True)
    report.update(status='passed' if all(p['status']=='passed' for p in report['projects']) else 'completed_with_failures',
                  completed_utc=datetime.now(timezone.utc).isoformat())
    save(out/'run.json',report)
    print(json.dumps(report),flush=True)

if __name__=='__main__':main()
