"""Offline integrity, complete-state and raw-sample audit; no new timing."""
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_fossil_feasibility import state
from test_alfio_roundtrip import compare_folders
from prototype.adaptive_planning.adapters import ExistingGateValidator,reviewed_framework
from prototype.adaptive_planning.description_adapter import SourceDescriptionValidator

def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main(suite,module):
    checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    index=load(suite/'run.json');check('all five fresh runs passed',index['status']=='passed' and len(index['projects'])==5)
    studies={r['project']:ROOT/r['evidence'][0] for r in index['projects']}
    for p,h in load(suite/'protocol.json')['frozen_sources'].items():check('frozen '+p,sha(ROOT/p)==h)
    for p,h in load(suite/'module-and-description-bindings.json')['sha256'].items():check('supplement '+p,sha(ROOT/p)==h)
    for name,p in list(studies.items())+[('modules',module)]:
        for f,h in load(p/'manifest.json').items():check(name+' manifest '+f,sha(p/f)==h)
    for name in ['java','beets','fossil']:
        p=studies[name].with_name(studies[name].name+'-audit.json')
        check(name+' separate raw audit passed',load(p)['status'] in ['pass','passed'])
    for i,row in enumerate(index['projects']):
        if i:check('sequential '+row['project'],index['projects'][i-1]['completed_utc']<=row['started_utc'])
    p=studies['alfio'];run=load(p/'run.json');summary=load(p/'summary.json')
    check('alfio retained A/A schedule',len(run['trials'])==16 and run['mode']=='original-original' and not run['cleanup_errors'])
    for trial in run['trials']:
        label=trial['label'];timings=load(p/(label+'-timing.json'))
        check(label+' exact raw durations',len(timings)==12 and sum(t['nanoseconds'] for t in timings)/1e6==trial['total_ms'])
        comparison=compare_folders(ROOT/'evidence/alfio-ordered-fusion/20261106-803c10c7d846/original',p/label)
        check(label+' complete 53 outputs/states',comparison['equal'] and comparison['compared_files']==53)
        check(label+' no candidate dispatch',load(p/(label+'-dispatch.json'))==[])
    for arm in ['original','retained']:
        value=statistics.median(t['total_ms'] for t in run['trials'] if t['phase']=='measured' and t['arm']==arm)
        check('alfio median '+arm,value==summary['metrics']['total_ms'][arm+'_median'])
    p=studies['oscar'];measurement=p/'measurement';records=load(measurement/'result.json')['runs']
    check('Oscar 20 trials',len(records)==20 and not load(p/'run.json')['cleanup_errors'])
    for pair in range(10):
        a=measurement/f'p{pair:02d}-original';b=measurement/f'p{pair:02d}-candidate'
        check('Oscar full initial '+str(pair),load(a/'before-full-state.json')==load(b/'before-full-state.json'))
        check('Oscar full final '+str(pair),load(a/'after-full-state.json')==load(b/'after-full-state.json'))
        ra,rb=load(a/'result.json'),load(b/'result.json')
        check('Oscar ordered outputs '+str(pair),ra['objects']==rb['objects'])
        for arm,r in [('original',ra),('candidate',rb)]:
            check('Oscar stored run '+str(pair)+arm,r==next(x for x in records if x['id']==r['id']))
            check('Oscar real commits '+str(pair)+arm,sum(x['calls']['commit'] for x in r['orders'])==4)
            check('Oscar calls '+str(pair)+arm,sum(x['calls']['execute'] for x in r['orders'])==(187 if arm=='original' else 183))
    for row in load(p/'summary.json')['pairs']:
        for arm in ['original','candidate']:
            r=load(measurement/f"p{row['pair']:02d}-{arm}"/'result.json')
            check('Oscar raw cost '+str(row['pair'])+arm,r['placement_ns']/1e6==row[arm+'_ms'])
    snapshots=load(ROOT/'evidence/fossil-hash-slice/20261110-v2/fixture-states.json')
    raw={r['id']:r for r in load(module/'runs.json')}
    check('module execution counts',len(raw)==508 and len(load(module/'correctness.json'))==306)
    for ident,r in raw.items():
        folder=module/f'run-{ident:04d}'
        check('module full data '+str(ident),state(folder/'repository.fossil')==snapshots[r['dataset']])
        check('module bound stdout/stderr '+str(ident),sha(folder/'stdout')==r['stdout_sha256'] and sha(folder/'stderr')==r['stderr_sha256'])
    for c in load(module/'correctness.json'):
        a,b=[raw[i] for i in c['runs']]
        check('module normal comparison '+str(c['runs']),c['equal'] and (a['exit'],a['stdout_sha256'],a['stderr_sha256'])==(b['exit'],b['stdout_sha256'],b['stderr_sha256']))
    for d in load(module/'diagnostics.json'):
        stderr=(module/f"run-{d['run']:04d}"/'stderr').read_bytes()
        check('module work counters '+str(d['run']),d['wiki_calls']==stderr.count(b'RDB:wiki-callback\n') and d['private_status_queries']==stderr.count(b'SELECT 1 FROM private WHERE'))
    for scenario in load(module/'summary.json')['performance']:
        for arm,value in scenario['median_ms'].items():
            samples=[raw[b['runs'][arm]['id']]['elapsed_ms'] for b in scenario['blocks']]
            check('module median '+scenario['request']+arm,statistics.median(samples)==value)
        for block in scenario['blocks']:
            for arm,r in block['runs'].items():check('module no diagnostic timing '+str(r['id']),r['timed'] and not r['probed'] and not r['environment'] and r==raw[r['id']])
        values=[b['runs']['projection_only']['elapsed_ms']+b['runs']['printer_only']['elapsed_ms']-b['runs']['original']['elapsed_ms']-b['runs']['both']['elapsed_ms'] for b in scenario['blocks']]
        check('module paired interaction '+scenario['request'],values==scenario['interaction']['values_ms'])
    planner=reviewed_framework(ROOT,validators={'deletion':ExistingGateValidator('deletion'),'fusion':ExistingGateValidator('fusion'),'source_region':SourceDescriptionValidator()})
    for folder in sorted((suite/'portfolios').iterdir()):
        if folder.is_dir():check('shared API replay '+folder.name,planner.select_catalog(load(folder/'catalog.json'))==load(folder/'selection.json'))
    result=dict(status='passed',checks=checks,subjects=5,module_executions=508,scope='Offline recomputation and recorded full-state checks; not independent semantic proof')
    (suite/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',checks=len(checks))))

if __name__=='__main__':main(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
