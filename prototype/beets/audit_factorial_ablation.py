#!/usr/bin/env python3
"""Recompute the four-arm experiment from archived databases, outputs and logs."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from test_beets_feasibility import snapshot


def main():
    out=Path(sys.argv[1]).resolve()
    load=lambda name:json.loads((out/name).read_text())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    checks=[]
    def check(name,ok):
        assert ok,name
        checks.append(name)
    manifest=load('manifest.json')
    check('all archived artifacts intact',all(sha(out/p)==h for p,h in manifest.items()))
    check('all referenced input evidence intact',all(sha(ROOT/p)==h for p,h in load('input-hashes.json').items()))
    check('source tree unchanged',all(sha(ROOT/'work/beets/source-e978874d'/p)==h for p,h in load('source-manifest.json').items()))
    media=load('media-manifest.json')
    check('all media unchanged',len(media)==5000 and all(sha(ROOT/p)==h for p,h in media.items()))
    report,protocol,runs,summary=[load(x+'.json') for x in ['report','protocol','runs','summary']]
    check('successful complete predeclared run',report['status']=='passed' and not (out/'failure.json').exists()
          and len(runs)==44 and len([r for r in runs if r['stage']=='formal'])==32
          and [{k:r[k] for k in ['id','stage','pair','arm']} for r in runs]==protocol['schedule'])
    arms=['original','query_only','model_only','full']
    positions=Counter();predecessors=Counter()
    for block in range(2,10):
        order=[r['arm'] for r in runs if r['stage']=='formal' and r['pair']==block]
        check(f'block {block} complete',sorted(order)==sorted(arms))
        positions.update((a,i) for i,a in enumerate(order))
        predecessors.update(zip(order,order[1:]))
    check('balanced formal positions and within-block predecessors',len(positions)==16 and set(positions.values())=={2}
          and len(predecessors)==12 and set(predecessors.values())=={2})
    state=load('template-state.json')
    check('template state matches five actual tables',snapshot(out/'template.db')==state and len(state['tables'])==5)
    state_hash=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    golden=sha(out/runs[0]['id']/'output.json')
    queries=dict(original=18002,query_only=13502,model_only=18002,full=13502)
    models=dict(original=14000,query_only=14000,model_only=9500,full=9500)
    for r in runs:
        folder=out/r['id']; data=json.loads((folder/'output.json').read_text())
        check(r['id']+' full database state',snapshot(folder/'library.db')==state and
              r['before_state_sha256']==r['after_state_sha256']==state_hash)
        check(r['id']+' byte-identical output',sha(folder/'output.json')==golden==r['output_sha256'])
        check(r['id']+' complete rows and fields',len(data)==5000 and
              [int(v['id']) for v in data]==list(range(1,5001)) and
              sum(v.get('albumtotal')=='17' for v in data)==4500 and
              all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=v.keys() for v in data))
        check(r['id']+' declared SQL work',r['queries']==queries[r['arm']])
        if r['stage']=='diagnostic':
            check(r['id']+' expected materialization and no timing',r['models']==models[r['arm']] and 'wall_ms' not in r)
        else:
            check(r['id']+' positive measured interval',0<r['wall_ms']<float('inf'))
    sql={a:load('diagnostic-'+a+'/queries.json') for a in arms}
    check('model elimination retains both exact SQL streams',sql['original']==sql['model_only'] and sql['query_only']==sql['full'])
    cursor=0;removed=[]
    for q in sql['original']:
        if cursor<len(sql['full']) and q==sql['full'][cursor]:cursor+=1
        else:removed.append(q)
    check('exactly private flex reads removed; remaining SQL and parameters ordered',cursor==len(sql['full'])
          and len(removed)==4500 and all('FROM item_attributes' in q['sql'] and 'album_id' in q['sql'] for q in removed))
    frozen=ROOT/'evidence/beets-prefix-export/20261109-44ae25a1b8a2'
    normalize=lambda p:ast.dump(ast.parse(p.read_text()).body[0],include_attributes=False)
    check('full cell is the exact previously validated candidate',normalize(out/'full-candidate.py')==normalize(frozen/'restored-candidate.py'))
    for name in ['reader.py','prefix.py','state.py','prefix_consumer.py','initialization_effects.py']:
        check(name+' frozen',sha(out/name)==sha(frozen/name))
    blocks={p:{r['arm']:r['wall_ms'] for r in runs if r['pair']==p and r['stage']=='formal'} for p in range(2,10)}
    for a in arms:
        check(a+' median recomputed',summary['arm_median_ms'][a]==statistics.median(b[a] for b in blocks.values()))
    for label,result in summary['comparisons'].items():
        a,b=label.split('__'); ratios=[v[a]/v[b] for v in blocks.values()]
        check(label+' paired contrasts recomputed',ratios==result['values'] and result['median']==statistics.median(ratios)
              and result['wins']==sum(r>1 for r in ratios))
    interactions=[b['query_only']+b['model_only']-b['original']-b['full'] for b in blocks.values()]
    check('within-block interaction recomputed',summary['interaction_saving_ms']['values']==interactions
          and summary['interaction_saving_ms']['median']==statistics.median(interactions))
    # Interval replication uses only the archived helper, after the point
    # estimates and contrasts above have been independently recomputed.
    import importlib.util
    spec=importlib.util.spec_from_file_location('archived_ablation',out/'beets_ablation_support.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    check('all exploratory intervals reproducible',module.summarize(runs)==summary==report['summary'])
    check('no production or backend admission',not report['production_changed'] and not report['shared_backend_admitted'])
    audit=dict(status='passed',checks=checks,executions=44,formal_executions=32,
        report_sha256=sha(out/'report.json'),audit_source_sha256=sha(Path(__file__)),
        scope='Offline evidence and statistics verification; no new timed execution')
    target=out.with_name(out.name+'-audit.json');target.write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(dict(audit=str(target),checks=len(checks))))


if __name__=='__main__':main()
