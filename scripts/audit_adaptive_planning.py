"""Audit imported measurements and bindings independently of strategy ranking."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.adaptive_planning.selector import select, fingerprint

def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('study',type=Path);ap.add_argument('controls',type=Path)
    args=ap.parse_args();out=args.study.resolve();controls=args.controls.resolve();checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    for folder in [out,controls]:
        for p,h in load(folder/'manifest.json').items():check('manifest '+folder.name+'/'+p,sha(folder/p)==h)
    check('tested selector is archived selector',sha(ROOT/'prototype/adaptive_planning/selector.py')==sha(out/'inputs/selector.py'))
    expected={'fossil-hash':'slice','fossil-branch':'slice','tpcc-mixed':'fusion_only','oscar-prefix':'original'}
    fossil=load(ROOT/'evidence/fossil-hash-slice/20261110-v2/summary.json')
    tpcc=load(ROOT/'evidence/tpcc-contract-slice/20261110-72f5adabbcb5/run.json')
    for name,wanted in expected.items():
        catalog=load(out/name/'catalog.json');result=load(out/name/'selection.json')
        check(name+' reproducible result',select(catalog,ROOT)==result)
        check(name+' expected strategy',result['selected']==wanted)
        check(name+' no deployment',result['automatic_deployment'] is False)
        check(name+' semantic eligibility first',result['admission'][wanted]['status']=='eligible')
        ctx=fingerprint(catalog['context'])
        for candidate in catalog['candidates']:
            cost=candidate.get('cost')
            if not cost:continue
            if name.startswith('fossil-'):
                scenario=next(s for s in fossil['performance'] if name=='fossil-'+s['name'])
                raw=[b['runs'][candidate['id']]['elapsed_ms'] for b in scenario['blocks']]
                blocks=list(range(len(raw)))
            else:
                cases=sorted([c for c in tpcc['cases'] if c['phase']=='measurement' and c['arm']==candidate['id']],key=lambda c:c['round'])
                raw=[sum(s['duration_ns'] for s in c['samples'])/1e6 for c in cases];blocks=[c['round'] for c in cases]
            check(name+'/'+candidate['id']+' exact raw measured costs',cost['values']==raw and cost['blocks']==blocks and cost['context']==ctx and cost['unit']=='ms')
        if wanted!='original':check(name+' supported baseline gain',result['comparisons'][wanted+'>original']['beats'])
    check('joint plan remains available but not automatically preferred',load(out/'tpcc-mixed/selection.json')['frontier']==['fusion_only','fusion_slice'])
    check('Oscar retains due to original checker',load(out/'oscar-prefix/selection.json')['admission']['ordered_fusion']['reason']=='existing_checker_retains_or_abstains')
    check('controls pass',load(controls/'summary.json')['status']=='passed')
    for freeze in ['paper/fossil-hash-slice-freeze.json','paper/tpcc-contract-slice-freeze.json']:
        for p,h in load(ROOT/freeze)['sha256'].items():check('previous freeze '+p,sha(ROOT/p)==h)
    result=dict(status='passed',checks=checks,selections=expected,no_new_timing=True,
                scope='Artifact and raw-cost audit plus reproducibility; not independent semantic proof or held-out evaluation')
    out.with_name(out.name+'-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',checks=len(checks))))


if __name__=='__main__':main()
