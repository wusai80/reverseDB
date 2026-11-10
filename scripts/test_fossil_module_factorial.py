"""Fixed module-level ablation of an existing admitted slice; no new optimizer."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.fossil.hash_slice import transform
from test_fossil_feasibility import state

BASE=ROOT/'evidence/fossil-hash-slice/20261110-v2'
BUILD=ROOT/'work/fossil/build-original'
ROUND=ROOT/'evidence/fossil-roundtrip/20261110-v1'
ARMS=['original','projection_only','printer_only','both']
ORDERS=[['original','projection_only','both','printer_only'],
        ['projection_only','printer_only','original','both'],
        ['printer_only','both','projection_only','original'],
        ['both','original','printer_only','projection_only']]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    checks=[];runs=[];commands=[];rejected={}
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    protocol=dict(schema='fossil-module-factorial/v1',arms=ARMS,
        factors={'projection_only':'Erase presentation-only SQL projections and omit their query-construction prepass; keep original C printer.',
                 'printer_only':'Keep original full SQL query/prepass; use existing specialized C hash printer.',
                 'both':'Exact existing combined slice; no change to optimizer or contract.'},
        baseline='Unmodified timeline source rebuilt with the same recovered DB wrappers used in every arm; compare with official original separately.',
        contract=load(BASE/'protocol.json'),warmup_blocks=2,measurement_blocks=8,formal_orders=ORDERS*2,
        requests=['hash','branch'],fixture='Complete existing 2000-commit fixture; no row or column deletion',
        timer='Whole fresh-process wall time including open/output/guard; copy and complete-state validation excluded',
        diagnostics='Probe binaries only; no probe/SQL trace during timing',
        rejection='Any normal output/state mismatch excludes that cell; retain raw mismatch, do not alter contract',
        scope='Module groups are semantic regions (SQL presentation production and C presentation consumption), not whole packages.',
        inference='Independent cost of grouped mechanisms, with paired interaction; not language-specific algorithm or automatic C proof')
    save(out/'protocol.json',protocol)
    try:
        for f,h in load(ROOT/'paper/fossil-hash-slice-freeze.json')['sha256'].items():check('old freeze '+f,sha(ROOT/f)==h)
        source=(BUILD/'bld/timeline_.c').read_bytes()
        both,plan=transform(source,load(ROOT/'evidence/fossil-effect-projection/20261110-v3/print-timeline-ast.json'),
            (ROOT/'evidence/fossil-recovery/20261110-v1/functions/timeline_query_for_tty.c').read_text(),
            load(ROOT/'prototype/fossil/display-contract.json'))
        check('combined source equals archived slice',both==(BASE/'slice.c').read_bytes())
        query=b'''  blob_append(&sql, reverseDbHashSlice ? reverse_db_hash_query()
                                     : timeline_query_for_tty(), -1);'''
        printer=b'''  if( reverseDbHashSlice ) reverse_db_print_hash_slice(&q, n);
  else print_timeline(&q, n, width, zFormat, verboseFlag);'''
        check('unique factor boundaries',both.count(query)==both.count(printer)==1)
        codes={'original':source,'projection_only':both.replace(printer,b'  print_timeline(&q, n, width, zFormat, verboseFlag);'),
               'printer_only':both.replace(query,b'  blob_append(&sql, timeline_query_for_tty(), -1);'),'both':both}
        save(out/'dependency-slice.json',plan)
        save(out/'module-map.json',dict(sql_presentation={'removed_projections':plan['replaced_columns'],'required':['row selection','ordering','UUID','user/date aliases','SQL lifecycle']},
            c_presentation={'removed':['comment/phase formatting','private-status lookups','format substitution'],'required':['full hash emission','row iteration','limits','notices']},
            protected=['persistent repository data','required query failures','argument validation','fallback output capabilities']))
        logs=(BUILD/'build.log').read_text().splitlines()
        def build(code,label):
            src=out/(label+'.c');src.write_bytes(code);obj=out/(label+'.o')
            cmd=shlex.split(next(s for s in logs if s.startswith('cc ') and s.endswith(' -c bld/timeline_.c')))
            cmd[cmd.index('-o')+1]=str(obj);cmd[-1]=str(src)
            link=shlex.split(next(s for s in reversed(logs) if s.startswith('cc ') and ' -o fossil ' in s))
            binary=out/('fossil-'+label);link[link.index('-o')+1]=str(binary)
            link=[str(obj) if s=='bld/timeline.o' else str(ROUND/'db.o') if s=='bld/db.o' else s for s in link]+[str(ROUND/'recovered.o')]
            for phase,argv in [('compile',cmd),('link',link)]:
                p=subprocess.run(argv,cwd=BUILD,capture_output=True,timeout=120)
                commands.append(dict(label=label,phase=phase,argv=argv,exit=p.returncode))
                (out/(label+'-'+phase+'.log')).write_bytes(p.stdout+p.stderr)
                check(label+' '+phase,p.returncode==0)
            return binary
        def probe(code):
            helper=b'''static int rdb_module_auth(void *u,int action,const char *a,const char *b,const char *c,const char *d){
 (void)u; (void)b; (void)c; (void)d;
 return action==SQLITE_READ && a && strcmp(a,"event")==0 ? SQLITE_DENY : SQLITE_OK;
}
'''
            code=code.replace(b'void print_timeline(',helper+b'void print_timeline(',1)
            marker=b'  zIn = (const char*)sqlite3_value_text(argv[0]);'
            assert code.count(marker)==1
            code=code.replace(marker,b'''  fprintf(stderr,"RDB:wiki-callback\\n");
  if(getenv("RDB_WIKI_ERROR")){sqlite3_result_error(context,"injected presentation-only failure",-1);return;}
'''+marker,1)
            code=code.replace(b'void timeline_cmd(void){',b'void timeline_cmd(void){\n  if(getenv("RDB_SEED")) sqlite3_test_control(SQLITE_TESTCTRL_PRNG_SEED,9973,(sqlite3*)0);',1)
            start=code.index(b'void timeline_cmd(void){')
            return code[:start]+code[start:].replace(b'  db_prepare_blob(&q, &sql);',b'  if(getenv("RDB_DENY_EVENT")) sqlite3_set_authorizer(g.db,rdb_module_auth,0);\n  db_prepare_blob(&q, &sql);',1)
        binaries={a:build(c,a) for a,c in codes.items()}
        probes={a:build(probe(c),'probe-'+a) for a,c in codes.items()}
        snapshots=load(BASE/'fixture-states.json')
        def run(arm,dataset,options,kind,*,probed=False,env_extra=None,origin=None,timed=False):
            folder=out/f'run-{len(runs)+1:04d}';folder.mkdir();(folder/'home').mkdir()
            db=folder/'repository.fossil';shutil.copyfile(BASE/'fixtures'/(dataset+'.fossil'),db)
            env=dict(os.environ,FOSSIL_HOME=str(folder/'home'),TZ='UTC',LC_ALL='C')
            for key in ['RDB_SEED','RDB_WIKI_ERROR','RDB_DENY_EVENT']:env.pop(key,None)
            env.update(env_extra or {})
            binary=(probes if probed else binaries)[arm] if arm!='official' else BUILD/'fossil'
            args=[str(binary),'timeline',*(origin or ['before','2030-01-01T00:00:00Z']),'-R',str(db),
                  *([] if '-n' in options else ['-n','0']),*([] if '-W' in options else ['-W','0']),'--utc',*options]
            start=time.perf_counter_ns();p=subprocess.run(args,cwd=folder,env=env,capture_output=True,timeout=45);elapsed=(time.perf_counter_ns()-start)/1e6
            (folder/'stdout').write_bytes(p.stdout);(folder/'stderr').write_bytes(p.stderr)
            r=dict(id=len(runs)+1,arm=arm,dataset=dataset,options=options,kind=kind,probed=probed,environment=env_extra or {},
                   timed=timed,elapsed_ms=elapsed,exit=p.returncode,stdout_sha256=sha(folder/'stdout'),stderr_sha256=sha(folder/'stderr'),
                   state_equal=state(db)==snapshots[dataset])
            runs.append(r);save(folder/'run.json',r)
            check('complete state '+str(r['id']),r['state_equal'])
            return p,r
        # Confirm reconstruction-only baseline before factoring the slice.
        for label,options in [('hash',['--format','%H']),('branch',['--format','%H','--branch','feature'])]:
            a,_=run('official','2000',options,'official-control');b,_=run('original','2000',options,'official-control')
            check('official baseline '+label,(a.returncode,a.stdout,a.stderr)==(b.returncode,b.stdout,b.stderr))
        correctness=[]
        for dataset in snapshots:
            for label,options in protocol['contract']['variants']:
                seed=dataset=='ties' and label=='fallback-default'
                origin=['after','2023-11-14T00:00:00Z'] if label.startswith('after') else None
                reference,rr=run('original',dataset,options,'correctness',probed=seed,env_extra={'RDB_SEED':'9973'} if seed else None,origin=origin)
                for arm in ARMS[1:]:
                    candidate,cr=run(arm,dataset,options,'correctness',probed=seed,env_extra={'RDB_SEED':'9973'} if seed else None,origin=origin)
                    equal=(reference.returncode,reference.stdout,reference.stderr)==(candidate.returncode,candidate.stdout,candidate.stderr)
                    correctness.append(dict(dataset=dataset,case=label,arm=arm,runs=[rr['id'],cr['id']],equal=equal))
                    if not equal:rejected.setdefault(arm,[]).append(correctness[-1])
        save(out/'correctness.json',correctness)
        diagnostics=[]
        for arm in ARMS:
            for label,opts,extra in [('work',['--format','%H','--sqltrace'],{}),
                ('required-query-failure',['--format','%H'],{'RDB_DENY_EVENT':'1'}),
                ('excluded-presentation-failure',['--format','%H'],{'RDB_WIKI_ERROR':'1'}),
                ('fallback-failure',['--format','%H|%c'],{'RDB_WIKI_ERROR':'1'})]:
                p,r=run(arm,'2000' if label=='work' else '100',opts,'diagnostic',probed=True,env_extra=extra)
                d=dict(arm=arm,case=label,run=r['id'],exit=p.returncode,wiki_calls=p.stderr.count(b'RDB:wiki-callback\n'),
                    private_status_queries=p.stderr.count(b'SELECT 1 FROM private WHERE'),
                    wiki_config_queries=sum(p.stderr.count(k) for k in [b"name='timeline-plaintext'",b"name='timeline-hard-newlines'",b"name='wiki-use-html'"]),
                    hash_config_queries=p.stderr.count(b"name='hash-digits'"))
                diagnostics.append(d)
                if label=='work':check('normal diagnostic '+arm,p.returncode==0)
                elif label=='required-query-failure':check('required query remains '+arm,p.returncode!=0 and b'prohibited' in p.stderr)
                elif label=='fallback-failure':check('fallback remains '+arm,p.returncode!=0 and d['wiki_calls']==1)
                else:check('declared excluded error '+arm,(p.returncode==0)==(arm in ['projection_only','both']))
        save(out/'diagnostics.json',diagnostics)
        active=[a for a in ARMS if a not in rejected];perf=[]
        for request,options in [('hash',['--format','%H']),('branch',['--format','%H','--branch','feature'])]:
            blocks=[]
            for block,order in enumerate((ORDERS[:2]+ORDERS*2)):
                values={}
                for arm in order:
                    if arm not in active:continue
                    p,r=run(arm,'2000',options,'warmup' if block<2 else 'measurement',timed=block>=2)
                    check('timed run success '+str(r['id']),p.returncode==0)
                    values[arm]=r
                check('timed output equality '+request+str(block),len({r['stdout_sha256'] for r in values.values()})==1)
                if block>=2:blocks.append(dict(block=block-2,order=[a for a in order if a in active],runs=values))
            medians={a:statistics.median(b['runs'][a]['elapsed_ms'] for b in blocks) for a in active}
            comparisons={}
            for a,b in [('original','projection_only'),('original','printer_only'),('original','both'),('projection_only','both'),('printer_only','both')]:
                if a not in active or b not in active:continue
                ratios=[r['runs'][a]['elapsed_ms']/r['runs'][b]['elapsed_ms'] for r in blocks]
                rng=random.Random(424242);samples=sorted(statistics.median(rng.choices(ratios,k=8)) for _ in range(20000))
                comparisons[a+'>'+b]=dict(median_paired_ratio=statistics.median(ratios),ratio_of_medians=medians[a]/medians[b],
                    wins=sum(x>1 for x in ratios),exploratory_95=[samples[499],samples[19499]])
            interaction=None
            if len(active)==4:
                values=[b['runs']['projection_only']['elapsed_ms']+b['runs']['printer_only']['elapsed_ms']-b['runs']['original']['elapsed_ms']-b['runs']['both']['elapsed_ms'] for b in blocks]
                rng=random.Random(424242);samples=sorted(statistics.median(rng.choices(values,k=8)) for _ in range(20000))
                interaction=dict(values_ms=values,median_ms=statistics.median(values),exploratory_95=[samples[499],samples[19499]],positive_means='extra savings beyond sum of isolated savings')
            perf.append(dict(request=request,blocks=blocks,median_ms=medians,comparisons=comparisons,interaction=interaction))
        save(out/'summary.json',dict(status='passed',checks=checks,rejected_cells=rejected,active=active,performance=perf,
            executions=len(runs),correctness_comparisons=len(correctness),all_data_preserved=True,automatic_native_proof=False))
        print(json.dumps(dict(status='passed',active=active,rejected_cells=rejected,performance=[{k:v for k,v in p.items() if k!='blocks'} for p in perf])),flush=True)
    except BaseException as e:
        save(out/'failure.json',dict(error=repr(e),checks=checks,executions=len(runs)));raise
    finally:
        save(out/'commands.json',commands);save(out/'runs.json',runs)
        shutil.copyfile(__file__,out/'runner.py')
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})

if __name__=='__main__':main()
