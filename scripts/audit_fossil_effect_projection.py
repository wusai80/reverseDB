"""Recompute study observations from archived files, without rerunning timing."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import statistics
from test_fossil_feasibility import state

ROOT = Path(__file__).resolve().parents[1]
def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('study',type=Path)
    out=parser.parse_args().study.resolve();checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    summary=load(out/'summary.json')
    for name,digest in load(out/'manifest.json').items():check('manifest '+name,sha(out/name)==digest)
    for name,digest in load(ROOT/'paper/staged-native-freeze.json')['sha256'].items():check('prior freeze '+name,sha(ROOT/name)==digest)
    fixtures=load(out/'fixture-states.json')
    records={}
    for path in sorted(out.glob('run-*/run.json')):
        r=load(path);records[r['id']]=r
        check(f'run {r["id"]} stdout binding',sha(path.parent/'stdout')==r['stdout_sha256'])
        check(f'run {r["id"]} all data',state(path.parent/'repository.fossil')==fixtures[r['dataset']])
    def bytes_(i,name):return (out/f'run-{i:04d}'/name).read_bytes()
    def canonical(i):
        text=bytes_(i,'stderr').decode()
        text=re.sub(r'/\* [^\n]*?ms, [^\n]*?vm-steps \*/','',text)
        text=re.sub(r'(?m)^-- sqlite3_open:.*\n','',text)
        return re.sub(r'(/\*\*\*\*\* Subprocess )\d+( exit\()',r'\1PID\2',text)
    check('40 correctness pairs',len(summary['whole_application_pairs'])==40)
    for a,b in summary['whole_application_pairs']:
        check(f'pair {a}/{b} output SQL exit',bytes_(a,'stdout')==bytes_(b,'stdout') and canonical(a)==canonical(b) and records[a]['exit']==records[b]['exit']==0)
    for d in summary['diagnostics']:
        counts=[];traces=[];errors=[]
        for i in d['runs']:
            lines=bytes_(i,'stderr').decode().splitlines()
            counts.append(dict(collections.Counter(x[4:] for x in lines if x.startswith('RDB:'))))
            traces.append([x for x in lines if x.startswith('RDB:') and x!='RDB:display-buffer'])
            errors.append([x for x in lines if not x.startswith('RDB:')])
        a,b=d['runs']
        check(f'diagnostic {a} counts',counts==d['counts'])
        check(f'diagnostic {a} traces/errors/output/exit',traces[0]==traces[1] and errors[0]==errors[1] and bytes_(a,'stdout')==bytes_(b,'stdout') and records[a]['exit']==records[b]['exit'])
    a,b=summary['negative_control']['runs']
    check('negative control hides effect error from output',bytes_(a,'stdout')==bytes_(b,'stdout') and b'hash-digits' in bytes_(a,'stderr') and b'hash-digits' not in bytes_(b,'stderr'))
    measurements=[]
    for p in summary['performance']:
        a=[];b=[]
        for block,pair in enumerate(p['pairs']):
            ra=records[pair['original']['id']];rb=records[pair['candidate']['id']]
            check(f'timing {p["variant"]}/{block} records and plain binaries',ra==pair['original'] and rb==pair['candidate'] and 'probe' not in ra['binary']+rb['binary'] and not ra['sql'] and not rb['sql'])
            check(f'timing {p["variant"]}/{block} AB/BA order',(ra['id']<rb['id'])==(block%2==0))
            check(f'timing {p["variant"]}/{block} output',ra['exit']==rb['exit']==0 and bytes_(ra['id'],'stdout')==bytes_(rb['id'],'stdout'))
            a.append(ra['elapsed_ms']);b.append(rb['elapsed_ms'])
        check('eight pairs '+p['variant'],len(a)==8)
        check('medians '+p['variant'],statistics.median(a)==p['original_median_ms'] and statistics.median(b)==p['candidate_median_ms'])
        measurements.append(dict(variant=p['variant'],original_median_ms=statistics.median(a),candidate_median_ms=statistics.median(b),
                                 median_latency_change_percent=100*(statistics.median(b)/statistics.median(a)-1),
                                 original_max_ms=max(a),candidate_max_ms=max(b),wins=sum(x>y for x,y in zip(a,b))))
    check('all 148 runs present',len(records)==summary['executions']==148)
    check('no whole native admission claimed',not summary['whole_native_admission'] and not summary['automatic_dp_selection'])
    result=dict(status='pass',checks=checks,measurements=measurements,study_manifest_sha256=sha(out/'manifest.json'),
                caveat='Short exploratory process timings; original hash arm contains a 41 ms outlier. No stable or large speedup claim.')
    target=out.with_name(out.name+'-audit.json');target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='pass',checks=len(checks),measurements=measurements)))


if __name__=='__main__':main()
