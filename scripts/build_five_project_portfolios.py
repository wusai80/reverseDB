"""Bind five-subject replication evidence to unchanged framework APIs/policy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.adaptive_planning.api import fingerprint
from prototype.adaptive_planning.adapters import ExistingGateValidator,reviewed_framework
from prototype.adaptive_planning.description_adapter import SourceDescriptionValidator

def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def ref(p):return dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def build(suite):
    index=load(suite/'run.json');assert index['status']=='passed',index['status']
    studies={r['project']:ROOT/r['evidence'][0] for r in index['projects']}
    out=suite/'portfolios';out.mkdir(exist_ok=False)
    old=ROOT/'evidence/adaptive-planning/20261110-v2'
    engines=load(old/'tpcc-mixed/catalog.json')['engine_bindings']+[ref(ROOT/p) for p in ['scripts/region_contracts.py','prototype/adaptive_planning/description_adapter.py']]
    planner=reviewed_framework(ROOT,validators={'deletion':ExistingGateValidator('deletion'),'fusion':ExistingGateValidator('fusion'),'source_region':SourceDescriptionValidator()})
    results={}
    def cost(folder,candidate,values,blocks,evidence):
        record=dict(context=candidate['context'],unit='ms',blocks=blocks,values=values)
        p=folder/(candidate['id']+'-cost.json');save(p,record)
        candidate['cost']=dict(record,binding=ref(p),evidence=[ref(evidence)])
    def clone(name,oldname,project):
        folder=out/name;folder.mkdir();c=load(old/oldname/'catalog.json')
        c['engine_bindings']=engines
        c['context']['deployment_digest']=fingerprint({'replication_protocol':ref(studies[project]/'protocol.json'),'suite':ref(suite/'protocol.json')})
        c['context']['measurement_scope']='Fresh fixed development-subject replication; not held-out subject transfer'
        c['context']['label']=name
        digest=fingerprint(c['context'])
        for candidate in c['candidates']:
            candidate['context']=digest;candidate['validation']['context']=digest
            candidate['validation']['evidence']=[ref(studies[project]/('summary.json' if project=='fossil' else 'run.json'))]
            candidate.pop('cost',None)
        return folder,c
    for name,oldname,project in [('java','tpcc-mixed','java'),('fossil-hash','fossil-hash','fossil'),('fossil-branch','fossil-branch','fossil'),('oscar','oscar-prefix','oscar')]:
        folder,c=clone(name,oldname,project);study=studies[project]
        for candidate in c['candidates']:
            arm=candidate['id']
            if project=='java':
                rows=sorted([r for r in load(study/'run.json')['cases'] if r['phase']=='measurement' and r['arm']==arm],key=lambda r:r['round'])
                cost(folder,candidate,[sum(s['duration_ns'] for s in r['samples'])/1e6 for r in rows],[r['round'] for r in rows],study/'run.json')
                mapped=[]
                for artifact in candidate['artifacts']:
                    path=ROOT/artifact['path'];prefix=ROOT/'evidence/tpcc-contract-slice/20261110-72f5adabbcb5'
                    if path.is_relative_to(prefix):path=study/path.relative_to(prefix)
                    mapped.append(ref(path))
                candidate['artifacts']=mapped
            elif project=='fossil':
                scenario=next(r for r in load(study/'summary.json')['performance'] if r['name']==name.split('-')[1])
                cost(folder,candidate,[b['runs'][arm]['elapsed_ms'] for b in scenario['blocks']],list(range(len(scenario['blocks']))),study/'summary.json')
                if arm=='slice':candidate['artifacts']=[ref(study/'fossil-slice'),ref(study/'slice.c')]
            else:
                rows=load(study/'summary.json')['pairs'];key='original_ms' if arm=='original' else 'candidate_ms'
                cost(folder,candidate,[r[key] for r in rows],[r['pair'] for r in rows],study/'summary.json')
        save(folder/'catalog.json',c);results[name]=planner.select_catalog(c);save(folder/'selection.json',results[name])
    for name in ['alfio','beets']:
        study=studies[name];folder=out/name;folder.mkdir()
        ctx=dict(label=name,source_digest=ref(ROOT/('prototype/alfio/assets.lock.json' if name=='alfio' else 'evidence/beets-feasibility/20261108-a03fafe1c20f/source-manifest.json'))['sha256'],
            contract_digest=fingerprint(load(study/'protocol.json')),workload_digest=fingerprint(load(study/'protocol.json')),
            deployment_digest=fingerprint({'protocol':ref(study/'protocol.json'),'suite':ref(suite/'protocol.json')}),
            timing_boundary='Cumulative service intervals with commits' if name=='alfio' else 'Complete direct ExportPlugin.run including adapter install/restore',
            measurement_scope='Fresh fixed development replication; A/A alfio, experimental beets candidate not automatically admitted')
        digest=fingerprint(ctx)
        original=dict(id='original',family='original',context=digest,authority='reviewed_experiment',regions=[],gates=[],
            artifacts=[ref(ROOT/('work/alfio/alfio-2.0-M5-2609-boot.jar' if name=='alfio' else 'work/beets/source-e978874d/beets/library/library.py'))],
            validation=dict(context=digest,evidence=[ref(study/('run.json' if name=='alfio' else 'report.json'))]))
        candidate=deepcopy(original);candidate['regions']=['supplied/region'];candidate.pop('cost',None)
        if name=='alfio':
            candidate.update(id='batch_fusion',family='fusion',gates=[dict(kind='fusion',input=ref(ROOT/'evidence/alfio-shared-placement/20261106-86a6bd34dc4c/nonempty-contract.json'))])
            candidate['artifacts']=[ref(ROOT/'prototype/alfio/ordered_batch.sql'),ref(ROOT/'prototype/alfio/AlfioOrderedBatch.java')]
            candidate['validation']['evidence']=[ref(ROOT/'evidence/alfio-ordered-fusion/20261106-803c10c7d846/run.json')]
            # A/A repeated retained arm is not a new optimization candidate.
            rows=load(study/'summary.json')['pairs']
            cost(folder,original,[r['original']['total_ms'] for r in rows],[r['pair'] for r in rows],study/'summary.json')
        else:
            candidate.update(id='representation_candidate',family='source_region',gates=[dict(kind='source_description',
                input=ref(ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b/beets-description.json'),
                expected=ref(ROOT/'evidence/common-evidence-admission/20261109-2e818b8dc3ee/beets-review.json'))])
            candidate['artifacts']=[ref(study/p) for p in ['candidate.py','reader.py','prefix.py','state.py','prefix_consumer.py']]
            rows=load(study/'summary.json')['pairs']
            cost(folder,original,[r['original_ms'] for r in rows],[r['pair'] for r in rows],study/'summary.json')
            cost(folder,candidate,[r['candidate_ms'] for r in rows],[r['pair'] for r in rows],study/'summary.json')
        c=dict(schema='adaptive-plan-portfolio/v1',context=ctx,engine_bindings=engines,candidates=[original,candidate])
        save(folder/'catalog.json',c);results[name]=planner.select_catalog(c);save(folder/'selection.json',results[name])
    save(out/'summary.json',dict(status='passed',subjects=5,contexts=6,selections={k:v['selected'] for k,v in results.items()},
        policy='unchanged development bootstrap policy; same APIs and validators; no project-name branch in framework'))
    save(out/'manifest.json',{str(p.relative_to(out)):ref(p)['sha256'] for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
    print(json.dumps({k:v['selected'] for k,v in results.items()}))

if __name__=='__main__':build(Path(sys.argv[1]).resolve())
