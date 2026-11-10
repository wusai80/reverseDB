#!/usr/bin/env python3
"""One fixed paired performance experiment; successful-read mechanism only."""
import ast
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from test_beets_feasibility import sha, snapshot

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/beets/source-e978874d'
BASE = ROOT / 'evidence/beets-feasibility/20261108-a03fafe1c20f'
VALIDATED = ROOT / 'evidence/beets-scalar-contract/20261108-0671127c5421'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SIZE = 5000
SEED = 20261109


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def summarize(records):
    pairs = []
    for pair in range(2, 10):
        arms = {r['arm']: r for r in records if r['stage'] == 'formal' and r['pair'] == pair}
        a, b = arms['original'], arms['candidate']
        pairs.append(dict(pair=pair, original_ms=a['wall_ms'], candidate_ms=b['wall_ms'],
                          speedup=a['wall_ms']/b['wall_ms']))
    ratios = [p['speedup'] for p in pairs]
    rng = random.Random(SEED)
    samples = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(20000))
    medians = {arm: statistics.median(r['wall_ms'] for r in records if r['stage']=='formal' and r['arm']==arm)
               for arm in ['original', 'candidate']}
    return dict(pairs=pairs, arm_median_ms=medians, paired_median_speedup=statistics.median(ratios),
        marginal_median_reduction_percent=100*(1-medians['candidate']/medians['original']),
        wins=sum(r>1 for r in ratios), paired_bootstrap_percentile95=[samples[499], samples[19499]],
        interval_method='20000 resamples of 8 paired ratios, median; seed 20261109; nearest-rank 2.5/97.5 percentiles',
        arm_range_ms={arm:[min(r['wall_ms'] for r in records if r['stage']=='formal' and r['arm']==arm),
                          max(r['wall_ms'] for r in records if r['stage']=='formal' and r['arm']==arm)] for arm in medians},
        arm_median_thread_cpu_ms={arm:statistics.median(r['thread_cpu_ms'] for r in records if r['stage']=='formal' and r['arm']==arm) for arm in medians},
        interpretation='Exploratory small-sample interval for this fixed deployment/workload; not a general speed guarantee')


def main():
    out = ROOT / 'evidence/beets-scalar-performance' / ('20261109-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    def load(path): return json.loads(path.read_text())
    def save(name, value):
        p = out/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    checks, records = [], []
    def check(name, condition):
        assert condition, name
        checks.append(name)
    schedule = [dict(id='diagnostic-'+arm, stage='diagnostic', pair=None, arm=arm) for arm in ['original','candidate']]
    for pair in range(10):
        for arm in (['original','candidate'] if pair%2==0 else ['candidate','original']):
            schedule.append(dict(id=f'p{pair:02d}-{arm}', pair=pair,
                                 stage='warmup' if pair<2 else 'formal', arm=arm))
    save('protocol.json', dict(schedule=schedule, items=SIZE, seed=SEED, warmup_pairs=2, formal_pairs=8,
        primary='Wall time for complete original ExportPlugin.run plus per-run candidate install/restore; all JSON file writing and media field access included',
        entry='Direct export --library JSON with id+ order, all default fields; not CLI process startup or plugin discovery',
        secondary='Current-thread CPU over same interval; SQLite is in process',
        timing_exclusions=['source validation and generated function compilation', 'fixture cloning and Library open',
                           'full-state/output validation', 'one-time dependency setup'],
        observer='Only Transaction.query integer counter in timed runs; full SQL and model observation only in two untimed diagnostic runs',
        cache='One process; fresh complete database clone and Library per execution; OS caches not flushed; predeclared diagnostics and warmups',
        domain='Pinned direct-export deployment, unchanged hooks/config; successful original reads; no claim of read-error equivalence or automatic fallback',
        data='Full prior 5000-item fixture, real WAV files and all flex attributes; album-track metadata tracktotal=17, identical in both arms',
        bootstrap=dict(resamples=20000, seed=SEED, statistic='median of paired original/candidate wall ratios'),
        stop='Run this fixed schedule once, retain all samples, no outlier deletion, no performance-driven retry or adjustment',
        admission='Experiment only; no production modification, runtime safety guard, new DP backend or universal equivalence claim'))
    shutil.copy2(__file__, out/'measure.py')
    shutil.copy2(ROOT/'scripts/test_beets_feasibility.py', out/'state-helper.py')
    try:
        check('explicit process seed', os.environ.get('PYTHONHASHSEED')==str(SEED))
        raw_manifest=load(VALIDATED/'manifest.json')
        check('complete validated mechanism evidence intact', all(sha(VALIDATED/p)==h for p,h in raw_manifest.items()))
        check('retained semantic experiment passed', load(VALIDATED/'report.json')['status']=='passed')
        source_manifest=load(BASE/'source-manifest.json')
        check('complete pinned upstream tree unchanged', all(sha(SOURCE/p)==h for p,h in source_manifest.items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged', sha(ROOT/'scripts'/f)==sha(FIRST/'inputs/scripts'/f))
        save('input-hashes.json', {str(p.relative_to(ROOT)):sha(p) for p in [BASE/'5000/library.db',
            BASE/'source-manifest.json', VALIDATED/'probe.py', VALIDATED/'drop-flex-and-model.py',
            VALIDATED/'report.json', VALIDATED/'manifest.json']})
        save('source-manifest.json', source_manifest)
        baseline_manifest=load(BASE/'manifest.json')
        media_manifest={str(p.relative_to(ROOT)):sha(p) for p in sorted((BASE/'5000/music').glob('*.wav'))}
        check('all 5000 original media files match retained manifest', len(media_manifest)==SIZE and all(
            h==baseline_manifest[str(Path(p).relative_to(BASE.relative_to(ROOT)))] for p,h in media_manifest.items()))
        save('media-manifest.json',media_manifest)
        (out/'config').mkdir()
        (out/'config/config.yaml').write_text('plugins: []\n')
        os.environ['BEETSDIR']=str(out/'config')
        sys.path.insert(0,str(SOURCE))
        import beets
        from beets import plugins
        from beets.dbcore import db as core, query
        from beets.library import Album, Item, Library
        from beetsplug.export import ExportPlugin
        beets.config['plugins']=[]
        beets.config['per_disc_numbering']=False
        plugin=ExportPlugin()
        check('fixed deployment excludes getter overrides', not list(plugins.find_plugins()) and 'tracktotal' not in Item._getters())
        check('fixed constructor', Item.__init__ is core.Model.__init__)
        save('runtime.json',dict(python=sys.version,sqlite=sqlite3.sqlite_version,beets=beets.__version__,
            source=beets.__file__,platform=platform.platform(),cpu_count=os.cpu_count(),load_average=os.getloadavg(),
            sort_item=beets.config['sort_item'].get(),per_disc_numbering=False,plugins=[],
            output_file_durability='Original export close behavior; no added fsync'))
        (out/'packages.txt').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze','--all']))

        # Execute the exact archived generated reader and candidate AST, rather
        # than reimplementing the already tested transformation for timing.
        generated=(VALIDATED/'drop-flex-and-model.py').read_text()
        namespace=dict(core.__dict__)
        exec(compile(generated,str(VALIDATED/'drop-flex-and-model.py'),'exec'),namespace)
        (out/'reader.py').write_text(generated)
        parsed=ast.parse((VALIDATED/'probe.py').read_text())
        fn=next(n for n in ast.walk(parsed) if isinstance(n,ast.FunctionDef) and n.name=='candidate')
        candidate_source=ast.unparse(fn)+'\n'
        (out/'candidate.py').write_text(candidate_source)
        original=Album._albumtotal
        fallbacks=[]
        bindings=dict(original=original,beets=beets,Item=Item,query=query,fallbacks=fallbacks,
                      readers={'drop-flex-and-model':namespace['read_fixed_rows']})
        exec(compile(candidate_source,str(out/'candidate.py'),'exec'),bindings)
        raw_candidate=bindings['candidate']
        def candidate(album): return raw_candidate(album,'drop-flex-and-model')
        check('candidate AST equals frozen tested function',ast.dump(ast.parse(candidate_source).body[0],include_attributes=False)==ast.dump(fn,include_attributes=False))
        with sqlite3.connect(BASE/'5000/library.db') as src, sqlite3.connect(out/'template.db') as dst:
            src.backup(dst)
            dst.execute('UPDATE items SET tracktotal=17 WHERE album_id IS NOT NULL')
        expected=snapshot(out/'template.db')
        save('template-state.json',expected)
        expected_hash=state_hash(expected)
        golden=None
        original_query=core.Transaction.query
        original_model=core.Results._make_model
        for trial in schedule:
            folder=out/trial['id'];folder.mkdir()
            path=folder/'library.db'
            with sqlite3.connect(out/'template.db') as src, sqlite3.connect(path) as dst:
                src.backup(dst)
            lib=Library(path,directory=BASE/'5000/music')
            before=snapshot(path)
            check(trial['id']+' complete starting state',before==expected)
            opts=SimpleNamespace(output=str(folder/'output.json'),append=False,format='json',library=True,
                                 album=False,included_keys=[],limit=None)
            counts={'queries':0,'models':0};queries=[]
            def count_query(tx,sql,params=()):
                counts['queries']+=1
                if trial['stage']=='diagnostic': queries.append(dict(sql=sql,params=list(params)))
                return original_query(tx,sql,params)
            def count_model(result,*args,**kwargs):
                counts['models']+=1
                return original_model(result,*args,**kwargs)
            fallbacks.clear()
            with ExitStack() as observers:
                observers.enter_context(patch.object(core.Transaction,'query',count_query))
                if trial['stage']=='diagnostic': observers.enter_context(patch.object(core.Results,'_make_model',count_model))
                start_wall=time.perf_counter_ns();start_cpu=time.thread_time_ns()
                with ExitStack() as installation:
                    if trial['arm']=='candidate': installation.enter_context(patch.object(Album,'_albumtotal',candidate))
                    plugin.run(lib,opts,['id+'])
                elapsed_cpu=time.thread_time_ns()-start_cpu;elapsed_wall=time.perf_counter_ns()-start_wall
            check(trial['id']+' original method restored',Album._albumtotal is original)
            after=snapshot(path)
            check(trial['id']+' all five tables and schema unchanged',after==expected)
            expected_calls=18002 if trial['arm']=='original' else 13502
            check(trial['id']+' actual query count',counts['queries']==expected_calls)
            check(trial['id']+' scalar path selected without fallback',not fallbacks)
            output_hash=sha(folder/'output.json')
            if golden is None: golden=output_hash
            check(trial['id']+' full output byte identity',output_hash==golden)
            data=load(folder/'output.json')
            check(trial['id']+' all rows and metadata retained',len(data)==SIZE and sum(x.get('albumtotal')=='17' for x in data)==4500
                  and all({'audit_note','probe_tag','rating_hint','filesize','has_cover_art'}<=x.keys() for x in data))
            record={**trial,'queries':counts['queries'],'output_sha256':output_hash,
                    'before_state_sha256':state_hash(before),'after_state_sha256':state_hash(after),
                    'output_bytes':(folder/'output.json').stat().st_size}
            if trial['stage']=='diagnostic':
                record['models']=counts['models']
                check(trial['id']+' expected materializations',counts['models']==(14000 if trial['arm']=='original' else 9500))
                save(trial['id']+'/queries.json',queries)
            else: record.update(wall_ms=elapsed_wall/1e6,thread_cpu_ms=elapsed_cpu/1e6)
            records.append(record);save('runs.json',records)
            lib._close()
            print(trial['id'],'completed',flush=True)
        check('exact fixed schedule completed',[(r['id'],r['arm']) for r in records]==[(r['id'],r['arm']) for r in schedule])
        check('full upstream tree remains unchanged',all(sha(SOURCE/p)==h for p,h in source_manifest.items()))
        check('all media bytes remain unchanged',all(sha(ROOT/p)==h for p,h in media_manifest.items()))
        summary=summarize(records);save('summary.json',summary)
        save('report.json',dict(status='passed',checks=checks,executions=len(records),formal_executions=16,
            full_state_sha256=expected_hash,summary=summary,strict_error_counterexamples_retained=True,
            production_changed=False,shared_backend_admitted=False,
            limits='Fixed successful-read direct-export mechanism benchmark; no CLI startup, concurrency, universal semantics or automatically selected backend claim'))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks,completed=len(records)))
        raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out,flush=True)


if __name__=='__main__': main()
