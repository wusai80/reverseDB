#!/usr/bin/env python3
"""Offline audit of the fixed complete-export records; never reruns exports."""
import ast
import copy
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from test_beets_feasibility import snapshot
from region_contracts import fingerprint, select_existing_binding


def main():
    out=Path(sys.argv[1]).resolve();checks=[]
    def load(p):return json.loads(p.read_text())
    def check(name,value):
        assert value,name
        checks.append(name)
    manifest=load(out/'manifest.json');protocol=load(out/'protocol.json');runs=load(out/'runs.json')
    check('all frozen artifacts intact',all(fingerprint(out/p)==h for p,h in manifest.items()))
    check('all input evidence intact',all(fingerprint(ROOT/p)==h for p,h in load(out/'input-hashes.json').items()))
    base=ROOT/'evidence/beets-feasibility/20261108-a03fafe1c20f'
    raw=ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'
    prefix=ROOT/'evidence/initialization-effects/20261109-d12bba955bf8'
    source=ROOT/'work/beets/source-e978874d'
    check('whole original application source intact',all(fingerprint(source/p)==h for p,h in load(base/'source-manifest.json').items()))
    media=load(out/'media-manifest.json')
    check('all 100 real media files unchanged',len(media)==100 and all(fingerprint(ROOT/p)==h for p,h in media.items()))
    check('original reader unchanged',(out/'reader.py').read_bytes()==(raw/'drop-flex-and-model.py').read_bytes())
    for name in ['initialization_effects.py','prefix_consumer.py']:
        current=ROOT/('scripts' if name=='initialization_effects.py' else 'prototype/beets')/name
        check(name+' identical to prior tested implementation',fingerprint(out/name)==fingerprint(prefix/name)==fingerprint(current))
    check('generated prefix and state retain exact earlier code',(out/'prefix.py').read_text()==load(prefix/'prefix-plan.json')['code'] and
          (out/'state.py').read_text()==load(prefix/'state-plan.json')['code'])
    old=ast.parse((out/'old-candidate.py').read_text()).body[0]
    archived=next(n for n in ast.walk(ast.parse((raw/'probe.py').read_text())) if isinstance(n,ast.FunctionDef) and n.name=='candidate')
    same=lambda a,b:ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)
    check('old control exactly matches frozen candidate',same(old,archived))
    new=ast.parse((out/'restored-candidate.py').read_text()).body[0]
    expected=copy.deepcopy(old)
    expected.body[-1]=ast.parse("return consume_packet(core, Item, rows, [], album._db, prefix, clear_state, ['tracktotal'])[0][0]").body[0]
    check('new candidate differs only by recovered-effect decode',same(new,expected))
    # Correct only the mistaken fixture count. Preserve the actual failed
    # report and its frozen runner; no exported data or execution trace changes.
    frozen=ast.parse((out/'test_beets_prefix_export.py').read_text())
    edits=0
    for n in ast.walk(frozen):
        if isinstance(n,ast.Dict):
            for key,value in zip(n.keys,n.values):
                if isinstance(key,ast.Constant) and key.value=='multidisc' and isinstance(value,ast.Constant) and value.value==81:
                    value.value=0;edits+=1
    check('working runner only corrects the count and comment',edits==1 and same(frozen,ast.parse((ROOT/'scripts/test_beets_prefix_export.py').read_text())))
    failure=load(out/'failure.json')
    check('preserved failure is solely the multidisc expectation',failure['type']=='AssertionError' and
          failure['message']=='multidisc declared reduction and fallback counts' and failure['completed']==7)
    schedule=[dict(case=case,arm=arm) for case,arms in [('normal',['original','old','restored']),
              ('multidisc',['original','restored']),('slow-sort',['original','restored'])] for arm in arms]
    check('exact predeclared seven exports completed',protocol['schedule']==schedule and
          [{k:r[k] for k in ['case','arm']} for r in runs]==schedule and len(runs)==7)
    baseline=snapshot(base/'100/library.db')
    # Validate every changed cell, not merely row counts or arm equality.
    with sqlite3.connect(base/'100/library.db') as db:
        cols={t:[r[1] for r in db.execute('PRAGMA table_info("'+t+'")')] for t in baseline['tables']}
    def expected_state(case):
        state=copy.deepcopy(baseline)
        for table,rows in state['tables'].items():
            fields=cols[table]
            for row in rows:
                values=dict(zip(fields,row))
                if table=='items' and values['album_id'] is not None:row[fields.index('tracktotal')]=17
                if case=='multidisc' and table=='items' and values['album_id']==1:
                    row[fields.index('disc')]=1 if values['track']<=5 else 2
                    row[fields.index('tracktotal')]=4 if values['track']<=5 else 6
                if case=='multidisc' and table=='albums' and values['id']==1:row[fields.index('disctotal')]=2
            rows.sort(key=lambda x:json.dumps(x,ensure_ascii=False))
        return state
    for case in ['normal','multidisc','slow-sort']:
        check(case+' all template cells equal declared changes only',snapshot(out/(case+'-template.db'))==load(out/(case+'-template-state.json'))==expected_state(case))
    with sqlite3.connect(out/'multidisc-template.db') as db:
        discs=db.execute('SELECT id,disctotal FROM albums ORDER BY id').fetchall()
    check('source fixture explains full fallback',discs==[(1,2)]+[(i,0) for i in range(2,11)])
    for run in runs:
        label=run['case']+'/'+run['arm'];folder=out/label
        check(label+' complete database unchanged',snapshot(folder/'library.db')==expected_state(run['case']))
        output=folder/'output.json';data=load(output);events=load(folder/'events.json');calls=load(folder/'calls.json')
        check(label+' actual full output/hash/size',len(data)==100 and [int(v['id']) for v in data]==list(range(1,101)) and
              fingerprint(output)==run['output_sha256'] and output.stat().st_size==run['output_bytes'])
        check(label+' all default fields retained',all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=v.keys() for v in data))
        album_rows=[v for v in data if v.get('album_id') and int(v['album_id'])>0]
        check(label+' metadata totals are not row count',len(album_rows)==90 and all(v['albumtotal']==('10' if run['case']=='multidisc' and int(v['album_id'])==1 else '17') for v in album_rows))
        check(label+' counts match actual traces',run['queries']==sum(e['kind']=='query' for e in events) and
              run['conversions']==sum(e['kind']=='convert' for e in events) and run['state_steps']==sum(e['kind']=='state' for e in events) and len(calls)==run['calls']==90)
        check(label+' observed native connection retained',run['connection_retained'])
        check(label+' source context positions',all(e['depth']==0 for e in events if e['kind'] in ['convert','state']))
        check(label+' no performance samples',not any(k in run for k in ['wall_ms','thread_cpu_ms','speedup']))
    summaries=[]
    for case in ['normal','multidisc','slow-sort']:
        a=load(out/case/'original/events.json');b=load(out/case/'restored/events.json');calls=load(out/case/'restored/calls.json')
        optimized={c['scope'] for c in calls if not c['fallbacks']}
        expected=[];removed=[]
        per_scope={c['scope']:[e for e in a if e['kind']=='query' and e['scope']==c['scope']] for c in calls}
        for event in a:
            if event['kind']=='query' and event['scope'] in optimized and event is per_scope[event['scope']][-1]:
                check(case+'/'+str(event['scope'])+' exact eligible second read',len(per_scope[event['scope']])==2 and 'FROM item_attributes' in event['sql'] and 'album_id' in event['sql'])
                removed.append(event)
            else:expected.append(event)
        check(case+' exact full event sequence after declared read removal',expected==b)
        check(case+' identical nonquery effects',[e for e in a if e['kind']!='query']==[e for e in b if e['kind']!='query'])
        check(case+' all output bytes equal',(out/case/'original/output.json').read_bytes()==(out/case/'restored/output.json').read_bytes())
        count=90 if case=='normal' else 0
        check(case+' independently corrected optimization count',len(optimized)==len(removed)==count)
        check(case+' exact existing fallback reasons',all(c['fallbacks']==([] if case=='normal' else ['multidisc' if case=='multidisc' else 'slow-sort']) for c in calls))
        summaries.append(dict(case=case,optimized=count,fallbacks=90-count,full_output_equal=True,ordered_retained_events_equal=True))
    normal={r['arm']:r for r in runs if r['case']=='normal'}
    a,b,c=[normal[k] for k in ['original','old','restored']]
    check('original 362 queries and 280 models',a['queries']==362 and a['models']==dict(Item=190,Album=90))
    check('both candidates 272 queries and 190 models',all(r['queries']==272 and r['models']==dict(Item=100,Album=90) for r in [b,c]))
    check('exactly 90 conversions and state steps restored',c['conversions']==a['conversions']==b['conversions']+90 and c['state_steps']==a['state_steps']==b['state_steps']+90)
    check('old/new complete output same',a['output_sha256']==b['output_sha256']==c['output_sha256'])
    original_events=load(out/'normal/original/events.json');old_events=load(out/'normal/old/events.json')
    check('old missing-effect witness retained',[e for e in original_events if e['kind']!='query']!=[e for e in old_events if e['kind']!='query'])
    # The original and old trace differ in exactly these observable prefixes.
    needed=[e for e in original_events if e['scope'] is not None and
            (e['kind']=='state' or (e['kind']=='convert' and e['field']=='mtime'))]
    check('old omission is precisely the recovered prefix and state steps',len(needed)==180 and
          [e for e in original_events if e['kind']!='query' and not (e['scope'] is not None and
           (e['kind']=='state' or (e['kind']=='convert' and e['field']=='mtime')))]==[e for e in old_events if e['kind']!='query'])
    common=ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b';first=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
    for name in ['neworder','payment','beets']:
        check(name+' original complete plan unchanged',select_existing_binding(load(common/(name+'-description.json')),ROOT)==load(common/(name+'-decision.json')))
    for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
        check(name+' original hash',fingerprint(ROOT/'scripts'/name)==fingerprint(first/'inputs/scripts'/name))
    result=dict(status='passed',checks=checks,executions=7,artifacts=len(manifest),comparisons=summaries,
        raw_evidence=str(out.relative_to(ROOT)),raw_status='Preserved post-execution assertion failure; corrected offline without rerunning exports',
        correction='Expected 81 optimized calls in multidisc; actual retained disctotal=0 makes all 90 correctly fall back',
        audit_source=dict(path=str(Path(__file__).relative_to(ROOT)),sha256=fingerprint(__file__)),
        corrected_runner_sha256=fingerprint(ROOT/'scripts/test_beets_prefix_export.py'),
        backend_admitted=False,performance_measured=False,production_changed=False,
        limits='Fixed successful-read deployment and observed effects; no whole-constructor, arbitrary receiver or deleted-read error proof')
    target=out.parent/(out.name+'-audit.json');target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status='passed',checks=len(checks),executions=7,artifacts=len(manifest),output=str(target))))


if __name__=='__main__':main()
