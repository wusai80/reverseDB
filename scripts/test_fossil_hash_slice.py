"""Contract-rooted slicing trial; SQL text is evidence, not an equivalence oracle."""
import argparse
import difflib
import hashlib
import itertools
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.fossil.hash_slice import transform, graph, REMOVED
from prototype.module_pruning.demand_model import decide, OBLIGATIONS
from test_fossil_feasibility import state

BUILD=ROOT/'work/fossil/build-original'
ROUND=ROOT/'evidence/fossil-roundtrip/20261110-v1'
PREVIOUS=ROOT/'evidence/fossil-effect-projection/20261110-v3'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True,type=Path)
    out=ap.parse_args().out.resolve();out.mkdir(parents=True,exist_ok=False)
    checks=[];commands=[];records=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    variants=[('hash',['--format','%H']),('limit',['--format','%H','-n','7']),
              ('line-limit',['--format','%H','-n','-7']),('quiet',['--format','%H','--quiet']),
              ('reverse',['--format','%H','--reverse']),('offset',['--format','%H','--offset','9']),
              ('branch',['--format','%H','--branch','feature']),('user',['--format','%H','--for-user','user1@example.invalid']),
              ('type',['--format','%H','--type','w']),('path',['--format','%H','--path','file-0.txt']),
              ('after',['--format','%H','-n','13']),('after-reverse',['--format','%H','-n','-13','--reverse']),
              ('fallback-default',[]),('fallback-full',['--full']),('fallback-comment',['--format','%H|%c|%p']),
              ('fallback-verbose',['--format','%H','-v']),('fallback-sql',['--format','%H','--sql'])]
    protocol=dict(contract='Exact full hashes, selection, ordering, limits/notices and complete persistent data; normal valid repository execution',
        activation='Exact %H; no verbose, SQL-as-output, local checkout or INT_MIN limit. All other modes use original path.',
        retained_failures=['argument validation','repository open/schema validation','retained query preparation/execution/finalization'],
        excluded=['unused presentation-only reads, caches and failures','allocation/resource exhaustion','undefined C behavior','concurrent mutation','internal SQL text/timing diagnostics'],
        change_from_previous='Presentation effects are no longer universal trace roots. This is a declared capability contract, not full legacy error equivalence.',
        correctness='Exact normal stdout/stderr/exit plus every repository table/schema. Required injected query errors compared by failure category.',
        nonce_case='ties/fallback-default uses diagnostic builds with SQLite PRNG seed 9973; production binaries never seed randomness',
        variants=variants,datasets=['100','2000','edge-default','edge-html','edge-plaintext','ties'],
        performance='Two 2000-row cases (hash, branch); original/roundtrip/slice; all six arm orders twice = 12 blocks each, no probes',
        bounds='Reviewed native dependency summary and lowering; unchanged existing demand algorithm; no general native frontend proof')
    save(out/'protocol.json',protocol)
    log=(BUILD/'build.log').read_text().splitlines()
    def build_run(argv,name):
        p=subprocess.run(argv,cwd=BUILD,capture_output=True,timeout=120)
        commands.append(dict(name=name,argv=argv,exit=p.returncode))
        (out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
        check(name,p.returncode==0)
    def build(code,name):
        src=out/(name+'.c');src.write_bytes(code);obj=out/(name+'.o')
        cmd=shlex.split(next(x for x in log if x.startswith('cc ') and x.endswith(' -c bld/timeline_.c')))
        cmd[cmd.index('-o')+1]=str(obj);cmd[-1]=str(src);build_run(cmd,'compile-'+name)
        cmd=shlex.split(next(x for x in reversed(log) if x.startswith('cc ') and ' -o fossil ' in x))
        target=out/('fossil-'+name);cmd[cmd.index('-o')+1]=str(target)
        cmd=[str(obj) if x=='bld/timeline.o' else str(ROUND/'db.o') if x=='bld/db.o' else x for x in cmd]
        cmd.append(str(ROUND/'recovered.o'));build_run(cmd,'link-'+name)
        return target
    try:
        # Check archived provenance and every pinned upstream source, not only
        # the function that receives an edit.
        for p,h in load(ROOT/'work/fossil/source-manifest.json').items():check('source '+p,sha(ROOT/'work/fossil/source-1573b8e6'/p)==h)
        for freeze in ['paper/staged-native-freeze.json','paper/fossil-effect-projection-freeze.json']:
            for p,h in load(ROOT/freeze)['sha256'].items():check('prior freeze '+p,sha(ROOT/p)==h)
        binding=load(ROOT/'prototype/fossil/display-contract.json')
        for p,h in binding['sources'].items():check('native binding '+p,sha(ROOT/p)==h)
        source=(BUILD/'bld/timeline_.c').read_bytes()
        candidate,plan=transform(source,load(PREVIOUS/'print-timeline-ast.json'),
            (ROOT/'evidence/fossil-recovery/20261110-v1/functions/timeline_query_for_tty.c').read_text(),binding)
        save(out/'dependency-slice.json',plan)
        (out/'candidate.diff').write_text(''.join(difflib.unified_diff(source.decode().splitlines(True),candidate.decode().splitlines(True),fromfile='original',tofile='hash-slice')))
        (out/'original-query.sql').write_text(plan['original_sql']);(out/'sliced-query.sql').write_text(plan['sliced_sql'])
        obligations={k:True for k in OBLIGATIONS}
        negatives=[]
        for facts,wanted in [({'hash_contract':False,'preserve_presentation_failures':False},'retain'),
                             ({'hash_contract':True,'preserve_presentation_failures':True},'retain'),
                             ({'hash_contract':True},'unknown')]:
            d=decide(graph(),facts,REMOVED,obligations);check('demand control '+str(facts),d['decision']==wanted);negatives.append(dict(facts=facts,decision=d))
        d=decide(graph(),{'hash_contract':True,'preserve_presentation_failures':False},REMOVED,{})
        check('incomplete certificate rejected',d['decision']=='unknown');negatives.append(d)
        try:transform(source+b'\n',load(PREVIOUS/'print-timeline-ast.json'),'invalid',binding)
        except ValueError:check('changed source rejected',True)
        else:check('changed source rejected',False)
        save(out/'negative-admission-controls.json',negatives)
        binaries={'original':BUILD/'fossil','roundtrip':ROUND/'fossil-roundtrip','slice':build(candidate,'slice')}
        # Probe-only callback error injection and SQLite authorizer failure.
        def probe(code):
            helper=b'''static void rdb_event(const char *key){ fprintf(stderr,"RDB:%s\\n",key); }
static int rdb_auth(void *u,int action,const char *a,const char *b,const char *c,const char *d){
  (void)u; (void)b; (void)c; (void)d;
  if(action==SQLITE_READ && a && strcmp(a,"event")==0){
    rdb_event("deny-event-read"); return SQLITE_DENY;
  }
  return SQLITE_OK;
}
'''
            code=code.replace(b'void print_timeline(',helper+b'void print_timeline(',1)
            marker=b'  zIn = (const char*)sqlite3_value_text(argv[0]);'
            check('callback probe unique',code.count(marker)==1)
            code=code.replace(marker,b'''  rdb_event("wiki-callback");
  if(getenv("RDB_WIKI_ERROR")){
    sqlite3_result_error(context,"injected presentation-only failure",-1); return;
  }
'''+marker,1)
            marker=b'static void reverse_db_print_hash_slice(Stmt *q, int nLimit){'
            code=code.replace(marker,marker+b'\n  rdb_event("hash-slice");',1)
            code=code.replace(b'void timeline_cmd(void){',b'void timeline_cmd(void){\n  if(getenv("RDB_SEED")) sqlite3_test_control(SQLITE_TESTCTRL_PRNG_SEED,9973,(sqlite3*)0);',1)
            start=code.index(b'void timeline_cmd(void){');prefix=code[:start];rest=code[start:]
            rest=rest.replace(b'  db_prepare_blob(&q, &sql);',b'  if(getenv("RDB_DENY_EVENT")) sqlite3_set_authorizer(g.db,rdb_auth,0);\n  db_prepare_blob(&q, &sql);',1)
            return prefix+rest
        probes={arm:build(probe(code),'probe-'+arm) for arm,code in [('original',source),('slice',candidate)]}
        fixtures=out/'fixtures';fixtures.mkdir()
        for p in sorted((PREVIOUS/'fixtures').glob('*.fossil')):shutil.copyfile(p,fixtures/p.name)
        shutil.copyfile(fixtures/'edge-default.fossil',fixtures/'ties.fossil')
        with sqlite3.connect(fixtures/'ties.fossil') as db:
            ids=[x[0] for x in db.execute('select objid from event order by objid')]
            db.execute('update event set mtime=2460263.5')
            uuid=db.execute('select uuid from blob where rid=?',(ids[0],)).fetchone()[0]
            for objid,comment in zip(ids[6:10],['+branch/feature','-checkin/'+uuid,':Modified','<verbatim type="pikchr">box "x"; arrow; box "y"</verbatim>']):
                db.execute('update event set comment=? where objid=?',(comment,objid))
        snapshots={p.stem:state(p) for p in sorted(fixtures.glob('*.fossil'))}
        save(out/'fixture-states.json',snapshots)
        def run(binary,dataset,options,name,env_extra=None,sql=False,timed=False,origin=None,repo_override=None):
            directory=out/f'run-{len(records)+1:04d}';directory.mkdir();(directory/'home').mkdir()
            db=directory/'repository.fossil';shutil.copyfile(fixtures/(dataset+'.fossil'),db)
            env=dict(os.environ,FOSSIL_HOME=str(directory/'home'),LC_ALL='C',TZ='UTC')
            for key in ['RDB_WIKI_ERROR','RDB_DENY_EVENT','RDB_SEED']:env.pop(key,None)
            env.update(env_extra or {})
            limit=[] if '-n' in options else ['-n','0']
            width=[] if '-W' in options else ['-W','0']
            cmd=[str(binary),'timeline',*(origin or ['before','2030-01-01T00:00:00Z']),'-R',str(repo_override or db),*limit,*width,'--utc',*options]
            if sql:cmd.append('--sqltrace')
            before=time.perf_counter_ns();p=subprocess.run(cmd,cwd=directory,env=env,capture_output=True,timeout=45);elapsed=(time.perf_counter_ns()-before)/1e6
            (directory/'stdout').write_bytes(p.stdout);(directory/'stderr').write_bytes(p.stderr)
            r=dict(id=len(records)+1,name=name,binary=str(binary.relative_to(ROOT)),dataset=dataset,options=options,origin=origin,
                   environment=env_extra or {},sql=sql,timed=timed,elapsed_ms=elapsed,exit=p.returncode,stdout_sha256=hashlib.sha256(p.stdout).hexdigest())
            records.append(r);save(directory/'run.json',r)
            check('complete state '+str(r['id']),state(db)==snapshots[dataset])
            return p,r
        pairs=[]
        for dataset in snapshots:
            for name,options in variants:
                origin=['after','2023-11-14T00:00:00Z'] if name.startswith('after') else None
                use_seed=dataset=='ties' and name=='fallback-default'
                arms=probes if use_seed else binaries
                extra={'RDB_SEED':'9973'} if use_seed else None
                a,ra=run(arms['original'],dataset,options,name,origin=origin,env_extra=extra)
                b,rb=run(arms['slice'],dataset,options,name,origin=origin,env_extra=extra)
                check(dataset+'/'+name,a.returncode==b.returncode==0 and a.stdout==b.stdout and a.stderr==b.stderr)
                pairs.append(dict(runs=[ra['id'],rb['id']],kind='normal'))
        # Graph traversal modes retain original TEMP tables and ancestry logic.
        with sqlite3.connect(fixtures/'100.fossil') as db:
            first,last=db.execute('select min(objid),max(objid) from event').fetchone()
            hashes=[db.execute('select uuid from blob where rid=?',(i,)).fetchone()[0] for i in [first,last]]
        for mode,uuid in [('descendants',hashes[0]),('ancestors',hashes[1])]:
            a,ra=run(binaries['original'],'100',['--format','%H','-n','13'],mode,origin=[mode,uuid])
            b,rb=run(binaries['slice'],'100',['--format','%H','-n','13'],mode,origin=[mode,uuid])
            check(mode,a.returncode==b.returncode==0 and a.stdout==b.stdout and a.stderr==b.stderr)
            pairs.append(dict(runs=[ra['id'],rb['id']],kind='traversal'))
        # Preserved entry errors, before any presentation work.
        for name,options,origin in [('invalid-width',['--format','%H','-W','1'],None),
                                    ('invalid-origin',['--format','%H'],['before','not-a-checkin'])]:
            a,ra=run(binaries['original'],'100',options,name,origin=origin)
            b,rb=run(binaries['slice'],'100',options,name,origin=origin)
            check(name,a.returncode==b.returncode!=0 and a.stdout==b.stdout and a.stderr==b.stderr)
            pairs.append(dict(runs=[ra['id'],rb['id']],kind='required-error'))
        diagnostics=[]
        for dataset,options,extra,name in [('2000',['--format','%H'],{},'removed-callback'),
            ('100',['--format','%H','-v'],{},'verbose-fallback'),('100',['--format','%H','--sql'],{},'sql-fallback'),
            ('100',['--format','%H'],{'RDB_DENY_EVENT':'1'},'retained-query-error'),
            ('100',['--format','%H'],{'RDB_WIKI_ERROR':'1'},'excluded-presentation-error'),
            ('100',['--format','%H|%c'],{'RDB_WIKI_ERROR':'1'},'fallback-preserves-presentation-error')]:
            a,ra=run(probes['original'],dataset,options,name,extra)
            b,rb=run(probes['slice'],dataset,options,name,extra)
            clean=lambda p:b'\n'.join(x for x in p.stderr.splitlines() if not x.startswith(b'RDB:'))
            counts=[p.stderr.count(b'RDB:wiki-callback\n') for p in [a,b]]
            if name=='removed-callback':check(name,counts==[2000,0] and a.stdout==b.stdout and a.returncode==b.returncode==0)
            elif name=='retained-query-error':check(name,a.returncode==b.returncode!=0 and not a.stdout and not b.stdout and all(b'prohibited' in p.stderr for p in [a,b]))
            elif name=='excluded-presentation-error':check(name,a.returncode!=0 and b.returncode==0 and counts==[1,0])
            else:check(name,a.returncode==b.returncode and a.stdout==b.stdout and clean(a)==clean(b) and counts[0]==counts[1])
            diagnostics.append(dict(name=name,runs=[ra['id'],rb['id']],wiki_calls=counts,exit_codes=[a.returncode,b.returncode]))
        # SQL changes are measured explicitly, not suppressed to manufacture a pass.
        sql_evidence=[]
        for arm in ['original','slice']:
            p,r=run(binaries[arm],'2000',['--format','%H'],'sql-diagnostic',sql=True)
            check('SQL diagnostic succeeds '+arm,p.returncode==0)
            sql_evidence.append(dict(arm=arm,run=r['id'],private_status_queries=p.stderr.count(b'SELECT 1 FROM private WHERE'),
                 wiki_config_queries=p.stderr.count(b"name='timeline-plaintext'")+p.stderr.count(b"name='timeline-hard-newlines'")+p.stderr.count(b"name='wiki-use-html'"),
                 hash_config_queries=p.stderr.count(b"name='hash-digits'")))
        check('presentation-only SQL removed',sql_evidence[0]['private_status_queries']==2000 and sql_evidence[1]['private_status_queries']==0 and sql_evidence[0]['wiki_config_queries']==3 and sql_evidence[1]['wiki_config_queries']==0)
        perf=[]
        schedule=list(itertools.permutations(['original','roundtrip','slice']))*2
        for name,options in [('hash',['--format','%H']),('branch',['--format','%H','--branch','feature'])]:
            for arm in binaries:run(binaries[arm],'2000',options,'warmup-'+name)
            blocks=[]
            for block,order in enumerate(schedule):
                results={}
                for arm in order:
                    p,r=run(binaries[arm],'2000',options,'timing-'+name,timed=True)
                    check('timing exit '+str(r['id']),p.returncode==0);results[arm]=r
                check('timing output '+name+str(block),len({r['stdout_sha256'] for r in results.values()})==1)
                blocks.append(dict(order=order,runs=results))
            medians={arm:statistics.median(b['runs'][arm]['elapsed_ms'] for b in blocks) for arm in binaries}
            perf.append(dict(name=name,blocks=blocks,median_ms=medians,
                slice_latency_change_percent=100*(medians['slice']/medians['original']-1),
                slice_wins=sum(b['runs']['slice']['elapsed_ms']<b['runs']['original']['elapsed_ms'] for b in blocks)))
        save(out/'summary.json',dict(status='pass',checks=checks,application_pairs=pairs,diagnostics=diagnostics,sql_evidence=sql_evidence,
            performance=perf,executions=len(records),whole_legacy_error_equivalence=False,whole_native_automation=False,
            demand_algorithm_sha256=sha(ROOT/'prototype/module_pruning/demand_model.py'),all_data_preserved=True))
        print(json.dumps(dict(status='pass',checks=len(checks),executions=len(records),pairs=len(pairs),
            performance=[{k:v for k,v in p.items() if k!='blocks'} for p in perf])))
    except BaseException as e:
        save(out/'failure.json',dict(type=type(e).__name__,message=str(e),checks=checks));raise
    finally:
        save(out/'commands.json',commands)
        inputs=out/'inputs';inputs.mkdir(exist_ok=True)
        for p in [Path(__file__),ROOT/'prototype/fossil/hash_slice.py',ROOT/'prototype/module_pruning/demand_model.py']:
            shutil.copyfile(p,inputs/p.name)
        save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':main()
