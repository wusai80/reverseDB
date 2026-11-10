"""Same fixed benchmark protocol, with preparation staged before execution.

Derived from measure_continuation; original harness and old results retained.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'evidence/beets-feasibility/20261108-a03fafe1c20f'
SOURCE=ROOT/'work/beets/source-e978874d'
SEED=20261110
SCENARIOS=['export-5000','scan-sparse','scan-dense']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True,allow_nan=False)+'\n')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
def clone(src,dst):
    with sqlite3.connect(src) as a,sqlite3.connect(dst) as b:a.backup(b)


def worker(out,trial):
    folder=out/trial['id'];folder.mkdir()
    source_hashes=json.loads((out/'input-hashes.json').read_text())
    if not all(sha(ROOT/p)==h for p,h in source_hashes.items()):raise AssertionError('Frozen source changed')
    sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(SOURCE))
    from test_beets_feasibility import snapshot,encode
    (folder/'config').mkdir();(folder/'config/config.yaml').write_text('plugins: []\n');os.environ['BEETSDIR']=str(folder/'config')
    import beets
    from beets import plugins
    from beets.dbcore import db as core
    from beets.dbcore.sort import FixedFieldSort
    from beets.library import Item,Album,Library
    from beetsplug.export import ExportPlugin
    from .staged_region import bind_region,Region
    from .export_adapter import make_adapter
    from . import export_adapter
    export_adapter.Region=Region  # Benchmark-only selection of the shared continuation runtime.
    from . import continuation as runtime_module
    beets.config['plugins']=[];beets.config['per_disc_numbering']=False
    if list(plugins.find_plugins()):raise AssertionError('Unexpected plugins')
    setup_start=time.perf_counter_ns();binding=bind_region(Item);plugin=ExportPlugin()
    parameters=[x['parameter'] for x in binding[1]['lazy'].values()]
    diagnostic=trial['stage']=='diagnostic'
    stats=dict(regions=0,materializations=0,original_result_paths=0,queries=0,models=0,demand_compilations=0,demand_compile_ms=0.0)
    adapted,adapter_source=make_adapter(Album._albumtotal,binding,stats if diagnostic else None)
    setup_ms=(time.perf_counter_ns()-setup_start)/1e6
    if diagnostic:
        save(folder/'binding.json',binding[-1]);(folder/'adapter.py').write_text(adapter_source)
    # Warm source/metadata/export machinery on a separate, complete 100-row DB.
    warm_db=folder/'warmup.db';clone(out/'template-100.db',warm_db)
    warm=Library(warm_db,directory=BASE/'100/music');warm_before=snapshot(warm_db)
    opts=SimpleNamespace(output=str(folder/'warmup.json'),append=False,format='json',library=True,album=False,included_keys=[],limit=None)
    with ExitStack() as stack:
        if trial['arm']=='candidate':stack.enter_context(patch.object(Album,'_albumtotal',adapted))
        plugin.run(warm,opts,['id+'])
    if len(json.loads((folder/'warmup.json').read_text()))!=100 or snapshot(warm_db)!=warm_before:raise AssertionError('Warmup changed data')
    warm._close()
    for k in stats:stats[k]=0.0 if k.endswith('_ms') else 0
    size=5000 if trial['scenario']=='export-5000' else 100
    dbpath=folder/'library.db';clone(out/('template-'+str(size)+'.db'),dbpath)
    lib=Library(dbpath,directory=BASE/str(size)/'music');expected=json.loads((out/('state-'+str(size)+'.json')).read_text())
    if snapshot(dbpath)!=expected:raise AssertionError('Incorrect initial state')
    queries=[];original_query=core.Transaction.query;original_model=core.Results._make_model;original_compile=runtime_module.compile_demand
    def count_query(tx,sql,params=()):
        stats['queries']+=1;queries.append(dict(sql=sql,params=encode(params)));return original_query(tx,sql,params)
    def count_model(obj,*args,**kwargs):stats['models']+=1;return original_model(obj,*args,**kwargs)
    def count_compile(b):
        start=time.perf_counter_ns()
        try:return original_compile(b)
        finally:stats['demand_compilations']+=1;stats['demand_compile_ms']+=(time.perf_counter_ns()-start)/1e6
    conversion=Item._convert
    scan_result=[];fault_ids=[];selected=[False]
    def fail_selected_album(cls,key,value):
        if selected[0] and key=='album':raise KeyError('benchmark album conversion')
        return conversion(key,value)
    def scan():
        # Both arms issue the same full fixed/flexible queries and consume all rows.
        result=lib.items(sort=FixedFieldSort('id'));rows=list(result.rows);flex=list(result.flex_rows)
        indexed=result._get_indexed_flex_attrs()
        for row in rows:
            selected[0]=row['album_id'] is not None and (trial['scenario']=='scan-dense' or row['id']%20==2)
            if selected[0]:fault_ids.append(row['id'])
            if trial['arm']=='original':
                obj=result._make_model(row,indexed.get(row['id'],{}));values=(obj.album,obj.tracktotal)
            else:
                fixed={k:v for k,v in dict(row).items() if k[:4]!='flex'}
                r=Region(binding,lib,{parameters[0]:fixed,parameters[1]:indexed.get(row['id'],{}) or {}})
                values=r.run(['album','tracktotal'])
                if diagnostic:stats['regions']+=1;stats['materializations']+=r.virtual.materializations
            scan_result.append(dict(id=row['id'],values=list(values)))
        selected[0]=False
    with ExitStack() as observers:
        if diagnostic:
            observers.enter_context(patch.object(core.Transaction,'query',count_query))
            observers.enter_context(patch.object(core.Results,'_make_model',count_model))
            observers.enter_context(patch.object(runtime_module,'compile_demand',count_compile))
        wall=time.perf_counter_ns();cpu=time.process_time_ns()
        with ExitStack() as installation:
            if trial['scenario']=='export-5000':
                if trial['arm']=='candidate':installation.enter_context(patch.object(Album,'_albumtotal',adapted))
                opts.output=str(folder/'output.json');plugin.run(lib,opts,['id+'])
            else:
                installation.enter_context(patch.object(Item,'_convert',classmethod(fail_selected_album)));scan()
        wall_ms=(time.perf_counter_ns()-wall)/1e6;cpu_ms=(time.process_time_ns()-cpu)/1e6
    if trial['scenario']!='export-5000':save(folder/'output.json',scan_result)
    after=snapshot(dbpath)
    if after!=expected:raise AssertionError('Complete database state changed')
    data=json.loads((folder/'output.json').read_text())
    if len(data)!=size:raise AssertionError('Output row count changed')
    if size==5000 and (sum(x.get('albumtotal')=='17' for x in data)!=4500 or not all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=x.keys() for x in data)):
        raise AssertionError('Incomplete export output')
    if trial['scenario']=='scan-sparse' and len(fault_ids)!=5:raise AssertionError('Sparse fault schedule differs')
    if trial['scenario']=='scan-dense' and len(fault_ids)!=90:raise AssertionError('Dense fault schedule differs')
    record=dict(**trial,wall_ms=wall_ms,process_cpu_ms=cpu_ms,setup_ms=setup_ms,rows=len(data),output_sha256=sha(folder/'output.json'),
        output_bytes=(folder/'output.json').stat().st_size,state_sha256=digest(after),fault_ids=fault_ids,
        max_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,max_rss_unit='bytes' if sys.platform=='darwin' else 'KiB',
        diagnostics=stats if diagnostic else None,queries_sha256=digest(queries) if diagnostic else None)
    if diagnostic:save(folder/'queries.json',queries)
    save(folder/'result.json',record);lib._close()


def summarize(records):
    result={}
    for scenario in SCENARIOS:
        pairs=[]
        for b in range(8):
            arms={r['arm']:r for r in records if r['stage']=='formal' and r['scenario']==scenario and r['block']==b}
            a,c=arms['original'],arms['candidate'];pairs.append(dict(block=b,original_ms=a['wall_ms'],candidate_ms=c['wall_ms'],speedup=a['wall_ms']/c['wall_ms']))
        logs=[math.log(p['speedup']) for p in pairs];rng=random.Random(SEED)
        bootstrap=sorted(math.exp(statistics.mean(rng.choices(logs,k=len(logs)))) for _ in range(20000))
        medians={arm:statistics.median(p[arm+'_ms'] for p in pairs) for arm in ['original','candidate']}
        result[scenario]=dict(pairs=pairs,median_ms=medians,paired_geometric_speedup=math.exp(statistics.mean(logs)),paired_median_speedup=statistics.median(p['speedup'] for p in pairs),
            candidate_latency_change_percent=100*(medians['candidate']/medians['original']-1),candidate_wins=sum(p['speedup']>1 for p in pairs),
            paired_bootstrap95=[bootstrap[499],bootstrap[19499]],
            interpretation='Exploratory paired interval, eight independent process blocks; no significance threshold or population-wide claim')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--worker');args=parser.parse_args()
    out=args.out.resolve()
    if args.worker:
        trial=next(t for t in json.loads((out/'protocol.json').read_text())['schedule'] if t['id']==args.worker)
        worker(out,trial);return
    out.mkdir(parents=True,exist_ok=False);records=[];checks=[]
    def check(n,c):
        if not c:raise AssertionError(n)
        checks.append(n)
    sys.path.insert(0,str(ROOT/'scripts'));from test_beets_feasibility import snapshot
    schedule=[]
    for scenario in SCENARIOS:
        for arm in ['original','candidate']:schedule.append(dict(id=scenario+'-diagnostic-'+arm,scenario=scenario,stage='diagnostic',block=None,arm=arm))
    for b in range(8):
        for scenario in SCENARIOS:
            for arm in (['original','candidate'] if b%2==0 else ['candidate','original']):
                schedule.append(dict(id=scenario+'-b'+str(b)+'-'+arm,scenario=scenario,stage='formal',block=b,arm=arm))
    save(out/'protocol.json',dict(schema='staged-continuation-performance/v1',predecessor='evidence/continuation-performance/20261110-v2',staging='Source validation and two demand compilations once per binding; live method/code/builtin guards per row; stable loaded source, no concurrent monkeypatching',seed=SEED,schedule=schedule,
        primary='Complete original ExportPlugin.run over 5000 full rows, all default fields and file output; original branch logic and both SQL queries retained',
        secondary='Complete 100-row album/tracktotal scan including original fixed/flexible queries; deterministic 5 or 90 conversion faults recovered via album fallback',
        primary_serialization='Full export serialization included; secondary scan result serialization excluded equally for both arms',
        independent_unit='Fresh subprocess and complete database clone per arm/block',formal_blocks=8,
        warmup='One full 100-row export in each process using a separate complete DB; OS caches not flushed',
        excluded=['process startup/import','one-time source binding','database cloning/open','warmup','post-run output/state validation'],
        included='All per-row live guards and continuation costs; installation/restoration included. One-time preparation measured separately in setup_ms',
        observers='No profiler or SQL/model counters in formal timing; separate diagnostics count queries, materialization and demand-compilation time',
        intervals='20000 paired process-block bootstrap resamples of mean log speedup, seed 20261110; exploratory 95% intervals, no binary significance claim',
        stop='Fixed schedule once; no outlier deletion, sample enlargement, or performance-driven code changes',
        timeout_per_worker_seconds=300,automatic_IR_admission=False,production_patch=False))
    try:
        check('fixed process seed',os.environ.get('PYTHONHASHSEED')==str(SEED))
        freeze_path=ROOT/'paper/continuation-freeze.json';freeze=json.loads(freeze_path.read_text())
        check('frozen continuation unchanged',all(sha(ROOT/p)==h for p,h in freeze['sha256'].items()))
        source_manifest=json.loads((BASE/'source-manifest.json').read_text())
        check('pinned beets source unchanged',all(sha(SOURCE/p)==h for p,h in source_manifest.items()))
        codefiles=sorted((ROOT/'prototype/lazy_state').glob('*.py'))+sorted((ROOT/'prototype/region_semantics').glob('*.py'))
        codefiles += [ROOT/'scripts/initialization_effects.py',ROOT/'scripts/test_beets_feasibility.py',freeze_path]
        hashes={str(p.relative_to(ROOT)):sha(p) for p in codefiles};save(out/'input-hashes.json',hashes)
        for p in codefiles:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
        media={str(p.relative_to(ROOT)):sha(p) for p in sorted((BASE/'5000/music').glob('*.wav'))}
        check('5000 complete media files',len(media)==5000);save(out/'media-hashes.json',media)
        baseline={str(BASE.relative_to(ROOT)/str(n)/'library.db'):sha(BASE/str(n)/'library.db') for n in [100,5000]};save(out/'baseline-hashes.json',baseline)
        for n in [100,5000]:
            path=out/('template-'+str(n)+'.db');clone(BASE/str(n)/'library.db',path)
            with sqlite3.connect(path) as db:db.execute('UPDATE items SET tracktotal=17 WHERE album_id IS NOT NULL')
            save(out/('state-'+str(n)+'.json'),snapshot(path))
        save(out/'runtime.json',dict(python=sys.version,sqlite=sqlite3.sqlite_version,platform=platform.platform(),cpu_count=os.cpu_count(),load_average=os.getloadavg()))
        golden={};diagnostics={}
        for trial in schedule:
            log=out/(trial['id']+'.log')
            with log.open('w') as stream:
                run=subprocess.run([sys.executable,'-m','prototype.lazy_state.measure_staged','--out',str(out),'--worker',trial['id']],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
            check(trial['id']+' worker passed',run.returncode==0)
            record=json.loads((out/trial['id']/'result.json').read_text());records.append(record);save(out/'runs.json',records)
            scenario=trial['scenario']
            if scenario not in golden:golden[scenario]=record['output_sha256']
            check(trial['id']+' complete output byte identity',record['output_sha256']==golden[scenario])
            if trial['stage']=='diagnostic':
                diagnostics.setdefault(scenario,{})[trial['arm']]=record
                if len(diagnostics[scenario])==2:
                    a,c=diagnostics[scenario]['original'],diagnostics[scenario]['candidate']
                    check(scenario+' identical complete SQL sequence',a['queries_sha256']==c['queries_sha256'])
                    expected_materializations=0 if scenario=='export-5000' else 5 if scenario=='scan-sparse' else 90
                    check(scenario+' exact diagnostic materializations',c['diagnostics']['materializations']==expected_materializations)
                    check(scenario+' no execution-time compilation',c['diagnostics']['demand_compilations']==0)
            print(trial['id']+' completed: '+str(round(record['wall_ms'],2))+' ms',flush=True)
        check('complete predetermined schedule',len(records)==len(schedule))
        check('all sources remain frozen',all(sha(ROOT/p)==h for p,h in hashes.items()))
        check('all original media and baseline DB bytes unchanged',all(sha(ROOT/p)==h for p,h in {**media,**baseline}.items()))
        result=summarize(records);save(out/'summary.json',result);save(out/'diagnostics.json',diagnostics)
        save(out/'report.json',dict(status='pass',checks=checks,executions=len(records),formal_executions=48,independent_blocks_per_scenario=8,
            all_outputs_and_complete_database_states_valid=True,prototype_changed=True,original_prototype_preserved=True,production_changed=False,shared_IR_admitted=False))
        print(json.dumps({s:{k:v for k,v in r.items() if k!='pairs'} for s,r in result.items()},indent=2),flush=True)
    except BaseException as e:
        save(out/'failure.json',dict(type=type(e).__name__,message=str(e),checks=checks,completed=len(records)));raise
    finally:
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
