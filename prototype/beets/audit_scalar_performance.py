#!/usr/bin/env python3
"""Verify the completed fixed performance protocol without rerunning exports."""
import ast
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from test_beets_feasibility import snapshot
from fusion_placement import choose_placement


def main():
    out=Path(sys.argv[1]).resolve()
    def load(name): return json.loads((out/name).read_text())
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    def state_hash(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append(name)
    manifest=load('manifest.json')
    check('all experiment artifacts intact',all(sha(out/p)==h for p,h in manifest.items()))
    check('all referenced evidence intact',all(sha(ROOT/p)==h for p,h in load('input-hashes.json').items()))
    check('original source tree intact',all(sha(ROOT/'work/beets/source-e978874d'/p)==h for p,h in load('source-manifest.json').items()))
    check('all 5000 original media files intact',len(load('media-manifest.json'))==5000 and
          all(sha(ROOT/p)==h for p,h in load('media-manifest.json').items()))
    check('measurement script and state helper frozen exactly',sha(out/'measure.py')==sha(ROOT/'scripts/measure_beets_scalar.py') and
          sha(out/'state-helper.py')==sha(ROOT/'scripts/test_beets_feasibility.py'))
    raw=ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'
    tree=ast.parse((raw/'probe.py').read_text())
    fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='candidate')
    check('exact validated candidate AST and reader',ast.dump(fn,include_attributes=False)==
          ast.dump(ast.parse((out/'candidate.py').read_text()).body[0],include_attributes=False) and
          (raw/'drop-flex-and-model.py').read_bytes()==(out/'reader.py').read_bytes())
    protocol,runs,report,summary=load('protocol.json'),load('runs.json'),load('report.json'),load('summary.json')
    expected_schedule=[dict(id='diagnostic-'+a,stage='diagnostic',pair=None,arm=a) for a in ['original','candidate']]
    for p in range(10):
        for a in (['original','candidate'] if p%2==0 else ['candidate','original']):
            expected_schedule.append(dict(id=f'p{p:02d}-{a}',stage='warmup' if p<2 else 'formal',pair=p,arm=a))
    check('predeclared AB/BA schedule exactly completed',protocol['schedule']==expected_schedule and
          [{k:r[k] for k in ['id','stage','pair','arm']} for r in runs]==expected_schedule)
    check('exact 22 executions, 16 formal; no failed run',len(runs)==22 and
          len([r for r in runs if r['stage']=='formal'])==16 and report['status']=='passed' and not (out/'failure.json').exists())
    state=load('template-state.json')
    check('template full snapshot matches actual database',snapshot(out/'template.db')==state and len(state['tables'])==5)
    digest=state_hash(state)
    check('reported full state fingerprint',report['full_state_sha256']==digest)
    golden=None
    for r in runs:
        prefix=r['id']
        check(prefix+' full real database unchanged',snapshot(out/prefix/'library.db')==state and
              r['before_state_sha256']==r['after_state_sha256']==digest)
        output=out/prefix/'output.json'
        h=sha(output)
        if golden is None: golden=h
        check(prefix+' complete output equality and byte count',h==golden==r['output_sha256'] and output.stat().st_size==r['output_bytes'])
        data=json.loads(output.read_text())
        check(prefix+' all rows, album metadata and flex/file fields',len(data)==5000 and
              [int(v['id']) for v in data]==list(range(1,5001)) and
              sum(v.get('albumtotal')=='17' for v in data)==4500 and
              all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=v.keys() for v in data))
        check(prefix+' query count',r['queries']==(18002 if r['arm']=='original' else 13502))
        if r['stage']=='diagnostic':
            check(prefix+' no diagnostic timing included', 'wall_ms' not in r and 'thread_cpu_ms' not in r)
            check(prefix+' model counts',r['models']==(14000 if r['arm']=='original' else 9500))
        else:
            check(prefix+' finite positive measured intervals',0<r['wall_ms']<float('inf') and 0<r['thread_cpu_ms']<float('inf'))
    a=load('diagnostic-original/queries.json');b=load('diagnostic-candidate/queries.json')
    pos=0;removed=[]
    for q in a:
        if pos<len(b) and q==b[pos]: pos+=1
        else: removed.append(q)
    check('only 4500 matching flex queries removed; all remaining SQL and params in original order',
          len(a)==18002 and len(b)==13502 and pos==len(b) and len(removed)==4500 and
          all('FROM item_attributes' in q['sql'] and 'album_id' in q['sql'] for q in removed))
    ratios=[]
    for p in range(2,10):
        a,b=[next(r for r in runs if r['pair']==p and r['arm']==arm) for arm in ['original','candidate']]
        ratio=a['wall_ms']/b['wall_ms'];ratios.append(ratio)
        check(f'pair {p} reported unchanged',summary['pairs'][p-2]==dict(pair=p,original_ms=a['wall_ms'],candidate_ms=b['wall_ms'],speedup=ratio))
    for arm in ['original','candidate']:
        values=[r['wall_ms'] for r in runs if r['stage']=='formal' and r['arm']==arm]
        cpus=[r['thread_cpu_ms'] for r in runs if r['stage']=='formal' and r['arm']==arm]
        check(arm+' medians/range recomputed',summary['arm_median_ms'][arm]==statistics.median(values) and
              summary['arm_range_ms'][arm]==[min(values),max(values)] and
              summary['arm_median_thread_cpu_ms'][arm]==statistics.median(cpus))
    check('paired median and wins recomputed',summary['paired_median_speedup']==statistics.median(ratios) and summary['wins']==sum(r>1 for r in ratios))
    rng=random.Random(protocol['bootstrap']['seed'])
    boot=sorted(statistics.median(rng.choices(ratios,k=8)) for _ in range(protocol['bootstrap']['resamples']))
    check('predeclared bootstrap recomputed',summary['paired_bootstrap_percentile95']==[boot[499],boot[19499]])
    a,b=[summary['arm_median_ms'][arm] for arm in ['original','candidate']]
    check('marginal median reduction recomputed',summary['marginal_median_reduction_percent']==100*(1-b/a))
    check('same report summary',report['summary']==summary)
    first=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
    for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
        check(name+' shared core frozen',sha(ROOT/'scripts'/name)==sha(first/'inputs/scripts'/name))
    for name in ['neworder','payment']:
        contract=json.loads((first/(name+'-contract.json')).read_text())
        prior=json.loads((first/(name+'-selection.json')).read_text())
        check(name+' same complete DP result',choose_placement(contract)==prior)
    check('read-error counterexamples still retained',all(not c['equal']['drop-flex-and-model'] for c in
          json.loads((raw/'cases.json').read_text()) if c['case'] in ['deny-flex','invalid-flex-text']))
    check('no production/backend admission',not report['production_changed'] and not report['shared_backend_admitted'])
    target=out.with_name(out.name+'-audit.json')
    target.write_text(json.dumps(dict(status='passed',checks=checks,artifacts=len(manifest),
        report_sha256=sha(out/'report.json'),audit_source_sha256=sha(Path(__file__)),
        scope='Offline complete-state, output, query, statistic, source and shared-selection verification; no new export timing'),indent=2)+'\n')
    print(json.dumps(dict(audit=str(target),checks=len(checks),artifacts=len(manifest))))


if __name__=='__main__': main()
