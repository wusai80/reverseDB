"""Bounded native effect-projection study. Production and probe builds separate."""
import argparse
import collections
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prototype.fossil.effect_projection import parse_ast, transform, format_effects
from test_fossil_feasibility import state

BUILD = ROOT / 'work/fossil/build-original'
BASE = ROOT / 'evidence/fossil-feasibility/20261110-v2'
ROUND = ROOT / 'evidence/fossil-roundtrip/20261110-v1'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', required=True, type=Path)
    out = parser.parse_args().out.resolve(); out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, result):
        if not result: raise AssertionError(name)
        checks.append(name)
    log = (BUILD / 'build.log').read_text().splitlines()
    def compile_command(module, source, target):
        cmd = shlex.split(next(x for x in log if x.startswith('cc ') and x.endswith(f' -c bld/{module}_.c')))
        cmd[cmd.index('-o')+1] = str(target); cmd[-1] = str(source)
        return cmd
    def build_run(cmd, name):
        p = subprocess.run(cmd, cwd=BUILD, capture_output=True, timeout=120)
        (out / (name+'.stdout')).write_bytes(p.stdout); (out / (name+'.stderr')).write_bytes(p.stderr)
        check(name, p.returncode == 0)
        return p.stdout.decode()
    def compile_module(module, source, name):
        src = out / (name+'.c'); src.write_bytes(source)
        obj = out / (name+'.o')
        build_run(compile_command(module, src, obj), 'compile-'+name)
        return obj
    def link(name, replacements):
        cmd = shlex.split(next(x for x in reversed(log) if x.startswith('cc ') and ' -o fossil ' in x))
        cmd = [str(replacements.get(x, x)) for x in cmd]
        target = out / name; cmd[cmd.index('-o')+1] = str(target)
        cmd.append(str(ROUND / 'recovered.o'))
        build_run(cmd, 'link-'+name)
        return target
    variants = [('default', []), ('full', ['--full']), ('hash', ['--format', '%H']),
                ('custom', ['--format', '%h|%c|%p|%u|%b|%t|%%|%Q']),
                ('branch', ['--full', '--branch', 'feature']),
                ('reverse', ['--full', '--reverse']), ('verbose', ['--full', '-v']),
                ('limit', ['--format', '%c', '-n', '7'])]
    save(out / 'protocol.json', dict(
        scope='One private display buffer; all SQLite/wiki computation retained',
        mechanism='Source-bound conditional effect projection with original-call fallback',
        datasets=['100', '2000', 'edge-default', 'edge-plaintext', 'edge-html'],
        variants=variants, correctness='Exact stdout/stderr/exit, all repository tables/schema, canonical SQL sequence',
        faults=['wiki callback sqlite3_result_error', 'synthetic hash_digits abrupt failure', 'synthetic hash-digits config failure'],
        negative='Deliberately omit hash effect; must fail trace comparison',
        performance='8 fixed AB/BA pairs per default/full/hash, 2000 commits, fresh processes; no probes',
        exclusions=['OOM', 'allocator address/count observability', 'undefined C execution', 'concurrent mutation'],
        not_claimed=['Whole native IR admission', 'Automatic DP selection', 'Arbitrary C proof', 'Removal of wiki or SQLite modules']))
    try:
        binding = json.loads((ROOT / 'prototype/fossil/display-contract.json').read_text())
        for path, expected in binding['sources'].items(): check('bound '+path, sha(ROOT/path) == expected)
        source = (BUILD/'bld/timeline_.c').read_bytes()
        cmd = compile_command('timeline', BUILD/'bld/timeline_.c', out/'unused.o')
        i=cmd.index('-o'); del cmd[i:i+2]; cmd.remove('-c')
        cmd += ['-Xclang', '-ast-dump=json', '-Xclang', '-ast-dump-filter=print_timeline', '-fsyntax-only']
        ast = parse_ast(build_run(cmd, 'clang-ast'), 'print_timeline')
        save(out/'print-timeline-ast.json', ast)
        candidate, evidence = transform(source, ast, binding)
        negative, _ = transform(source, ast, binding, preserve_effects=False)
        save(out/'def-use-and-effects.json', evidence)
        (out/'candidate.diff').write_text(''.join(difflib.unified_diff(source.decode().splitlines(True), candidate.decode().splitlines(True), fromfile='original', tofile='candidate')))
        for bad in ['%n', '%z', '%W', '%!S', '%*s', '%']:
            try: format_effects(bad, ['arg'])
            except ValueError: check('reject format '+bad, True)
            else: check('reject format '+bad, False)
        try: transform(source+b'\n', ast, binding)
        except ValueError: check('reject changed source', True)
        else: check('reject changed source', False)
        replacements = {'bld/db.o': ROUND/'db.o'}
        candidate_binary = link('fossil-candidate', dict(replacements, **{'bld/timeline.o': compile_module('timeline', candidate, 'timeline-candidate')}))

        # Diagnostic code is never linked into timed production binaries.
        probe_header = b'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void rdb_probe(const char *key){
  const char *fail = getenv("REVERSEDB_FAIL");
  fprintf(stderr, "RDB:%s\\n", key);
  if(fail && strcmp(fail,key)==0){
    fflush(stdout); fprintf(stderr,"RDBFAULT:%s\\n",key); fflush(stderr); _Exit(87);
  }
}
'''
        printf_source = (BUILD/'bld/printf_.c').read_bytes()
        printf_source = printf_source.replace(b'int hash_digits(int bForUrl){', b'int hash_digits(int bForUrl){\n  rdb_probe("hash_digits");', 1)
        printf_source = printf_source.replace(b'char *mprintf(const char *zFormat, ...){', b'char *mprintf(const char *zFormat, ...){\n  if(strncmp(zFormat,"[%S]",4)==0) rdb_probe("display-buffer");', 1)
        printf_obj = compile_module('printf', probe_header+printf_source, 'printf-probe')
        db_source = (ROUND/'db-without-targets.c').read_bytes()
        for marker in [b'int db_get_int(const char *zName, int dflt){', b'int db_get_boolean(const char *zName, int dflt){']:
            check('probe anchor '+marker.decode(), db_source.count(marker)==1)
            db_source=db_source.replace(marker,marker+b'\n  rdb_probe(zName);',1)
        db_obj=compile_module('db',probe_header+db_source,'db-probe')
        probes={}
        for arm, code in [('roundtrip',source),('candidate',candidate),('negative',negative)]:
            marker=b'  zIn = (const char*)sqlite3_value_text(argv[0]);'
            check('wiki probe unique '+arm,code.count(marker)==1)
            code=code.replace(marker,b'''  rdb_probe("wiki_to_text");
  if(getenv("REVERSEDB_WIKI_ERROR")){
    sqlite3_result_error(context,"injected wiki callback error",-1); return;
  }
'''+marker,1)
            obj=compile_module('timeline',probe_header+code,'timeline-probe-'+arm)
            probes[arm]=link('fossil-probe-'+arm,{'bld/db.o':db_obj,'bld/printf.o':printf_obj,'bld/timeline.o':obj})
        fixtures=out/'fixtures'; fixtures.mkdir()
        for size in ['100','2000']:
            shutil.copyfile(BASE/size/'repository.fossil',fixtures/(size+'.fossil'))
        # Synthetic event/config variations; no rows or artifacts removed.
        for label, settings in [('edge-default',{}),('edge-plaintext',{'timeline-plaintext':'1','hash-digits':'4'}),
                                 ('edge-html',{'wiki-use-html':'1','timeline-hard-newlines':'1','hash-digits':'100'})]:
            path=fixtures/(label+'.fossil');shutil.copyfile(fixtures/'100.fossil',path)
            with sqlite3.connect(path) as db:
                ids=[r[0] for r in db.execute('select objid from event order by objid')]
                uuid=db.execute('select uuid from blob where rid=?',(ids[0],)).fetchone()[0]
                comments=[None,'','café 中文 😀',f'[{uuid}|artifact] [MissingPage] [2020-01-01] [deadbeef] [https://example.invalid|external]',
                          '<b>unterminated &amp; <nowiki>[bad]</nowiki>\nnext', 'x'*70000, '+新增页面','-Deleted',':Edited']
                for i,comment in enumerate(comments):
                    db.execute('update event set comment=?, ecomment=NULL, type=?, user=?, euser=NULL where objid=?',
                               (comment,'w' if i>=6 else 'ci',None if i==2 else '作者',ids[i]))
                for key,value in settings.items():
                    db.execute('insert or replace into config(name,value,mtime) values(?,?,0)',(key,value))
        expected={p.stem:state(p) for p in sorted(fixtures.glob('*.fossil'))}
        save(out/'fixture-states.json',expected)
        serial=0
        def execute(binary,dataset,options,env_extra=None,sql=False,timed=False):
            nonlocal serial
            serial+=1; directory=out/f'run-{serial:04d}';directory.mkdir();(directory/'home').mkdir()
            db=directory/'repository.fossil';shutil.copyfile(fixtures/(dataset+'.fossil'),db)
            env=dict(os.environ,FOSSIL_HOME=str(directory/'home'),LC_ALL='C',TZ='UTC')
            # Parent-shell injection settings must not change normal runs.
            env.pop('REVERSEDB_FAIL',None);env.pop('REVERSEDB_WIKI_ERROR',None)
            env.update(env_extra or {})
            limit=[] if '-n' in options else ['-n','0']
            cmd=[str(binary),'timeline','before','2030-01-01T00:00:00Z','-R',str(db),*limit,'-W','0','--utc',*options]
            if sql:cmd.append('--sqltrace')
            start=time.perf_counter_ns();p=subprocess.run(cmd,cwd=directory,env=env,capture_output=True,timeout=30)
            elapsed=(time.perf_counter_ns()-start)/1e6
            (directory/'stdout').write_bytes(p.stdout);(directory/'stderr').write_bytes(p.stderr)
            record=dict(id=serial,binary=str(binary.relative_to(ROOT)),dataset=dataset,options=options,exit=p.returncode,
                        elapsed_ms=elapsed,timed=timed,sql=sql,environment=env_extra or {},stdout_sha256=hashlib.sha256(p.stdout).hexdigest())
            save(directory/'run.json',record)
            check(f'run {serial} all repository data/schema',state(db)==expected[dataset])
            return p,record
        def canonical(data):
            text=data.decode().replace(str(out),'OUT')
            text=re.sub(r'/\* [^\n]*?ms, [^\n]*?vm-steps \*/','',text)
            text=re.sub(r'(?m)^-- sqlite3_open:.*\n','',text)
            text=re.sub(r'(/\*\*\*\*\* Subprocess )\d+( exit\()',r'\1PID\2',text)
            return text
        cases=[]
        for dataset in expected:
            for name,opts in variants:
                a,ar=execute(BUILD/'fossil',dataset,opts,sql=True)
                b,br=execute(candidate_binary,dataset,opts,sql=True)
                check(f'{dataset}/{name} output exit SQL',a.returncode==b.returncode==0 and a.stdout==b.stdout and canonical(a.stderr)==canonical(b.stderr))
                cases.append([ar['id'],br['id']])
        diagnostics=[]
        def trace(p):return [x for x in p.stderr.decode().splitlines() if x.startswith('RDB:') and x!='RDB:display-buffer']
        for dataset,opts,extra in [('2000',['--format','%H'],{}),('edge-default',['--format','%H'],{}),
                                  ('100',[],{}),('100',['--format','%H'],{'REVERSEDB_FAIL':'hash_digits'}),
                                  ('100',['--format','%H'],{'REVERSEDB_FAIL':'hash-digits'}),
                                  ('100',['--format','%H'],{'REVERSEDB_WIKI_ERROR':'1'})]:
            a,ar=execute(probes['roundtrip'],dataset,opts,extra)
            b,br=execute(probes['candidate'],dataset,opts,extra)
            check(f'probe {ar["id"]} observable output exit retained effects',a.returncode==b.returncode and a.stdout==b.stdout and trace(a)==trace(b))
            clean=lambda p:b'\n'.join(x for x in p.stderr.splitlines() if not x.startswith(b'RDB:'))
            check(f'probe {ar["id"]} errors',clean(a)==clean(b))
            if extra:check(f'probe {ar["id"]} fault activated',a.returncode!=0)
            counts=[dict(collections.Counter(x[4:] for x in p.stderr.decode().splitlines() if x.startswith('RDB:'))) for p in [a,b]]
            if dataset=='2000':check('2000 discarded buffers; wiki and hash retained',counts[0].get('display-buffer')==2000 and counts[1].get('display-buffer',0)==0 and counts[0]['wiki_to_text']==counts[1]['wiki_to_text']==2000)
            if dataset=='edge-default':check('long-string original-call fallback exercised',counts[1].get('display-buffer',0)>0)
            diagnostics.append(dict(runs=[ar['id'],br['id']],counts=counts))
        a,ar=execute(probes['roundtrip'],'100',['--format','%H'])
        b,br=execute(probes['negative'],'100',['--format','%H'])
        check('naive deletion negative control detected',trace(a)!=trace(b))
        negative_control=dict(runs=[ar['id'],br['id']],same_stdout=a.stdout==b.stdout,trace_equal=trace(a)==trace(b))
        # Freeze performance schedule above; no adaptive repetition.
        perf=[]
        for name,opts in variants[:3]:
            for binary in [BUILD/'fossil',candidate_binary]:execute(binary,'2000',opts) # warmups
            pairs=[]
            for block in range(8):
                pair={}
                for arm in (['original','candidate'] if block%2==0 else ['candidate','original']):
                    p,r=execute(BUILD/'fossil' if arm=='original' else candidate_binary,'2000',opts,timed=True)
                    check('timing run succeeds '+str(r['id']),p.returncode==0)
                    pair[arm]=r
                check('timing pair output '+str(block)+' '+name,pair['original']['stdout_sha256']==pair['candidate']['stdout_sha256'])
                pairs.append(pair)
            ratios=[p['original']['elapsed_ms']/p['candidate']['elapsed_ms'] for p in pairs]
            perf.append(dict(variant=name,pairs=pairs,original_median_ms=statistics.median(p['original']['elapsed_ms'] for p in pairs),
                             candidate_median_ms=statistics.median(p['candidate']['elapsed_ms'] for p in pairs),
                             geometric_speedup=math.exp(statistics.mean(map(math.log,ratios))),wins=sum(r>1 for r in ratios)))
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('shared algorithm unchanged '+name,sha(ROOT/'scripts'/name)==sha(ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts'/name))
        save(out/'summary.json',dict(status='pass',checks=checks,whole_application_pairs=cases,diagnostics=diagnostics,
             negative_control=negative_control,performance=perf,executions=serial,source_bound_experiment=True,
             whole_native_admission=False,automatic_dp_selection=False,all_data_preserved=True))
        print(json.dumps(dict(status='pass',checks=len(checks),executions=serial,performance=[{k:v for k,v in p.items() if k!='pairs'} for p in perf])))
    except BaseException as e:
        save(out/'failure.json',dict(type=type(e).__name__,message=str(e),checks=checks));raise
    finally:
        inputs=out/'inputs';inputs.mkdir(exist_ok=True)
        for path in [Path(__file__),ROOT/'prototype/fossil/effect_projection.py',ROOT/'prototype/fossil/display-contract.json']:
            shutil.copyfile(path,inputs/path.name)
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
