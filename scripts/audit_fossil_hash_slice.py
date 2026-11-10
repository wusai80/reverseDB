"""Independent artifact audit of contract-rooted Fossil slicing."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics
from test_fossil_feasibility import state

ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('study',type=Path);out=ap.parse_args().study.resolve()
    checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    for name,h in load(out/'manifest.json').items():check('manifest '+name,sha(out/name)==h)
    for file in ['paper/staged-native-freeze.json','paper/fossil-effect-projection-freeze.json']:
        for name,h in load(ROOT/file)['sha256'].items():check('old freeze '+name,sha(ROOT/name)==h)
    summary=load(out/'summary.json');snapshots=load(out/'fixture-states.json');runs={}
    for p in sorted(out.glob('run-*/run.json')):
        r=load(p);runs[r['id']]=r
        check('stdout binding '+str(r['id']),sha(p.parent/'stdout')==r['stdout_sha256'])
        check('full data '+str(r['id']),state(p.parent/'repository.fossil')==snapshots[r['dataset']])
    def raw(i,stream):return (out/f'run-{i:04d}'/stream).read_bytes()
    check('304 runs',len(runs)==summary['executions']==304)
    check('106 output/error pairs',len(summary['application_pairs'])==106)
    for case in summary['application_pairs']:
        a,b=case['runs'];ra=runs[a];rb=runs[b]
        check(f'pair {a}/{b}',ra['exit']==rb['exit'] and raw(a,'stdout')==raw(b,'stdout') and raw(a,'stderr')==raw(b,'stderr'))
        check(f'pair {a} expected exit',(ra['exit']!=0)==(case['kind']=='required-error'))
    for d in summary['diagnostics']:
        a,b=d['runs'];counts=[raw(i,'stderr').count(b'RDB:wiki-callback\n') for i in [a,b]]
        check(d['name']+' counts',counts==d['wiki_calls'])
        check(d['name']+' exits',[runs[a]['exit'],runs[b]['exit']]==d['exit_codes'])
        if d['name']=='removed-callback':check('whole callback path removed',counts==[2000,0] and raw(a,'stdout')==raw(b,'stdout'))
        if d['name']=='retained-query-error':check('retained SQLite authorization failure',all(runs[i]['exit']!=0 and b'prohibited' in raw(i,'stderr') for i in [a,b]))
        if d['name']=='excluded-presentation-error':check('semantic boundary explicit',[runs[a]['exit'],runs[b]['exit']]==[1,0])
    for item in summary['sql_evidence']:
        data=raw(item['run'],'stderr')
        check('SQL counts '+item['arm'],item['private_status_queries']==data.count(b'SELECT 1 FROM private WHERE') and
              item['wiki_config_queries']==sum(data.count(x) for x in [b"name='timeline-plaintext'",b"name='timeline-hard-newlines'",b"name='wiki-use-html'"]) and
              item['hash_config_queries']==data.count(b"name='hash-digits'"))
    measurements=[]
    for scenario in summary['performance']:
        expected=list(itertools.permutations(['original','roundtrip','slice']))*2
        medians={arm:statistics.median(block['runs'][arm]['elapsed_ms'] for block in scenario['blocks']) for arm in expected[0]}
        check('medians '+scenario['name'],medians==scenario['median_ms'])
        check('12 fixed blocks '+scenario['name'],len(scenario['blocks'])==12)
        for j,block in enumerate(scenario['blocks']):
            check('balanced order '+scenario['name']+str(j),block['order']==list(expected[j]))
            ids=[block['runs'][arm]['id'] for arm in block['order']]
            check('actual order '+str(ids),ids==sorted(ids))
            for arm,r in block['runs'].items():
                check('plain timing '+str(r['id']),r==runs[r['id']] and r['timed'] and not r['sql'] and 'probe' not in r['binary'] and not r['environment'] and r['exit']==0)
            check('timed bytes '+str(ids),len({raw(i,'stdout') for i in ids})==1)
        measurements.append(dict(name=scenario['name'],median_ms=medians,
            median_speedup=medians['original']/medians['slice'],
            latency_reduction_percent=100*(1-medians['slice']/medians['original']),wins=scenario['slice_wins']))
    check('unchanged demand algorithm',sha(ROOT/'prototype/module_pruning/demand_model.py')==summary['demand_algorithm_sha256']==sha(out/'inputs/demand_model.py'))
    for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
        check('unchanged first-project algorithm '+name,sha(ROOT/'scripts'/name)==sha(ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts'/name))
    check('limits stated',not summary['whole_native_automation'] and not summary['whole_legacy_error_equivalence'])
    result=dict(status='pass',checks=checks,measurements=measurements,manifest_sha256=sha(out/'manifest.json'))
    out.with_name(out.name+'-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='pass',checks=len(checks),measurements=measurements)))


if __name__=='__main__':main()
