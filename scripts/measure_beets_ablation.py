#!/usr/bin/env python3
"""Fixed four-arm ablation: query elimination x effect-preserving model elimination."""
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
from beets_ablation_support import ARMS, QUERY_COUNTS, MODEL_COUNTS, schedule as make_schedule, variants, summarize

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/beets/source-e978874d'
BASE = ROOT / 'evidence/beets-feasibility/20261108-a03fafe1c20f'
VALIDATED = ROOT / 'evidence/beets-scalar-contract/20261108-0671127c5421'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
RESTORED = ROOT / 'evidence/beets-prefix-export/20261109-44ae25a1b8a2'
RESTORED_AUDIT = RESTORED.parent / (RESTORED.name + '-audit.json')
SIZE = 5000
SEED = 20261109


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()



def main():
    out = ROOT / 'evidence/beets-factorial-ablation' / ('20261109-' + uuid4().hex[:12])
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
    schedule = make_schedule()
    save('protocol.json', dict(arms=ARMS, schedule=schedule, items=SIZE, seed=SEED,
        factors=['remove private flex query', 'remove private Item, retaining initialization/control/state operations'],
        cells=dict(original=[0,0], query_only=[1,0], model_only=[0,1], full=[1,1]),
        primary='Complete ExportPlugin.run plus per-run install/restore, original file reads and JSON writing/close',
        timing_exclusions=['source checks/compilation', 'fixture clone/Library open', 'full output/state validation', 'CLI startup/plugin discovery'],
        order='Two warmup blocks then eight four-arm Williams balanced blocks; each arm twice in each formal position; every ordered pair twice as within-block neighbors',
        observer='Same query counter in timed runs; full SQL and model counts only in four untimed diagnostics',
        cache='One process, fresh complete database per execution; OS caches not flushed',
        data='Unchanged 5000-item, five-table fixture, all flex metadata and media; common tracktotal=17 fixture preparation',
        domain='Fixed direct export, no plugins, successful reads; no deleted-read error equivalence claim',
        comparisons=['original/query_only', 'original/model_only', 'original/full', 'model_only/full', 'query_only/full'],
        interaction='T(query_only)+T(model_only)-T(original)-T(full), within block',
        bootstrap=dict(resamples=20000, seed=SEED, statistic='paired median, exploratory not multiplicity adjusted'),
        warmup_blocks=2, formal_blocks=8, diagnostics=4, total_executions=44,
        stop='Execute once; retain every sample; no tuning, outlier removal or performance-dependent repetition',
        admission='Isolated mechanism ablation; no production or shared-backend change'))
    shutil.copy2(ROOT/'scripts/beets_ablation_support.py', out/'beets_ablation_support.py')
    shutil.copy2(__file__, out/'measure.py')
    shutil.copy2(ROOT/'scripts/test_beets_feasibility.py', out/'state-helper.py')
    try:
        check('explicit process seed', os.environ.get('PYTHONHASHSEED')==str(SEED))
        raw_manifest=load(VALIDATED/'manifest.json')
        check('complete validated mechanism evidence intact', all(sha(VALIDATED/p)==h for p,h in raw_manifest.items()))
        check('retained semantic experiment passed', load(VALIDATED/'report.json')['status']=='passed')
        check('whole restored integration archive intact',all(sha(RESTORED/p)==h for p,h in load(RESTORED/'manifest.json').items()))
        restored_audit=load(RESTORED_AUDIT)
        check('restored complete-export audit passed',restored_audit['status']=='passed' and restored_audit['executions']==7 and
              restored_audit['raw_evidence']==str(RESTORED.relative_to(ROOT)))
        check('restored audit source and runner association intact',sha(ROOT/restored_audit['audit_source']['path'])==restored_audit['audit_source']['sha256'] and
              sha(ROOT/'scripts/test_beets_prefix_export.py')==restored_audit['corrected_runner_sha256'])
        check('consumer exception dependency unchanged',sha(ROOT/'scripts/initialization_effects.py')==sha(RESTORED/'initialization_effects.py'))
        source_manifest=load(BASE/'source-manifest.json')
        check('complete pinned upstream tree unchanged', all(sha(SOURCE/p)==h for p,h in source_manifest.items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged', sha(ROOT/'scripts'/f)==sha(FIRST/'inputs/scripts'/f))
        save('input-hashes.json', {str(p.relative_to(ROOT)):sha(p) for p in [BASE/'5000/library.db',
            BASE/'source-manifest.json', VALIDATED/'probe.py', VALIDATED/'drop-flex-and-model.py',
            VALIDATED/'report.json', VALIDATED/'manifest.json', RESTORED_AUDIT, RESTORED/'manifest.json',
            *[RESTORED/p for p in ['restored-candidate.py','reader.py','prefix.py','state.py','prefix_consumer.py','initialization_effects.py']]]})
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
        check('fixed deployment excludes getter overrides', not list(plugins.find_plugins()) and not {'mtime','tracktotal'} & Item._getters().keys())
        check('fixed constructor', Item.__init__ is core.Model.__init__)
        save('runtime.json',dict(python=sys.version,sqlite=sqlite3.sqlite_version,beets=beets.__version__,
            source=beets.__file__,platform=platform.platform(),cpu_count=os.cpu_count(),load_average=os.getloadavg(),
            sort_item=beets.config['sort_item'].get(),per_disc_numbering=False,plugins=[],
            output_file_durability='Original export close behavior; no added fsync'))
        (out/'packages.txt').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze','--all']))

        # Execute the exact archived generated reader and candidate AST, rather
        # than reimplementing the already tested transformation for timing.
        generated=(RESTORED/'reader.py').read_text()
        namespace=dict(core.__dict__)
        exec(compile(generated,str(RESTORED/'reader.py'),'exec'),namespace)
        (out/'reader.py').write_text(generated)
        parsed=ast.parse((RESTORED/'restored-candidate.py').read_text())
        fn=next(n for n in ast.walk(parsed) if isinstance(n,ast.FunctionDef) and n.name=='candidate')
        candidate_source=ast.unparse(fn)+'\n'
        (out/'candidate.py').write_text(candidate_source)
        original=Album._albumtotal
        fallbacks=[]
        bindings=dict(original=original,beets=beets,Item=Item,query=query,fallbacks=fallbacks,
                      readers={'drop-flex-and-model':namespace['read_fixed_rows']},core=core)
        for filename,symbol,target in [('prefix.py','run_initialization_prefix','prefix'),
                                      ('state.py','run_private_state_step','clear_state'),
                                      ('prefix_consumer.py','consume_packet','consume_packet')]:
            code=(RESTORED/filename).read_text();(out/filename).write_text(code)
            module={};exec(compile(code,str(out/filename),'exec'),module)
            bindings[target]=module[symbol]
        shutil.copy2(RESTORED/'initialization_effects.py',out/'initialization_effects.py')
        exec(compile(candidate_source,str(out/'candidate.py'),'exec'),bindings)
        raw_candidate=bindings['candidate']
        def candidate(album): return raw_candidate(album,'drop-flex-and-model')
        candidates=variants(RESTORED,core,bindings,out)
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
                    if trial['arm']!='original': installation.enter_context(patch.object(Album,'_albumtotal',candidates[trial['arm']]))
                    plugin.run(lib,opts,['id+'])
                elapsed_cpu=time.thread_time_ns()-start_cpu;elapsed_wall=time.perf_counter_ns()-start_wall
            check(trial['id']+' original method restored',Album._albumtotal is original)
            after=snapshot(path)
            check(trial['id']+' all five tables and schema unchanged',after==expected)
            expected_calls=QUERY_COUNTS[trial['arm']]
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
                check(trial['id']+' expected materializations',counts['models']==MODEL_COUNTS[trial['arm']])
                save(trial['id']+'/queries.json',queries)
            else: record.update(wall_ms=elapsed_wall/1e6,thread_cpu_ms=elapsed_cpu/1e6)
            records.append(record);save('runs.json',records)
            lib._close()
            print(trial['id'],'completed',flush=True)
        check('exact fixed schedule completed',[(r['id'],r['arm']) for r in records]==[(r['id'],r['arm']) for r in schedule])
        check('full upstream tree remains unchanged',all(sha(SOURCE/p)==h for p,h in source_manifest.items()))
        check('all media bytes remain unchanged',all(sha(ROOT/p)==h for p,h in media_manifest.items()))
        summary=summarize(records);save('summary.json',summary)
        save('report.json',dict(status='passed',checks=checks,executions=len(records),formal_executions=32,
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
