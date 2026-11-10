#!/usr/bin/env python3
"""Failure-driven original-program screen. No optimization or timing retuning."""
from collections import defaultdict
from contextlib import ExitStack
import cProfile
import functools
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import py_compile
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from types import CodeType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import wave

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'work/beets/source-e978874d'
COMMIT='e978874d198311c51e54beb7f3df060035a10153'
TEMP=Path('/tmp/reversedb-next-project')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def encode(v):
    if isinstance(v,bytes):return {'bytes':v.hex()}
    if isinstance(v,(list,tuple)):return [encode(x) for x in v]
    return v
def snapshot(path):
    with sqlite3.connect(path) as db:
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables={}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            q='"'+name.replace('"','""')+'"'
            tables[name]=sorted([encode(row) for row in db.execute('SELECT * FROM '+q)],key=lambda x:json.dumps(x,ensure_ascii=False))
    return dict(schema=encode(schema),tables=tables)


class Meter:
    """Exclusive nested observer intervals; observations perturb execution."""
    def __init__(self):
        self.stack=[];self.ns=defaultdict(int);self.calls=defaultdict(int);self.queries=[]
    def wrap(self,category,fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            item=[time.perf_counter_ns(),0];self.stack.append(item)
            try:return fn(*args,**kwargs)
            finally:
                elapsed=time.perf_counter_ns()-item[0];self.stack.pop()
                self.ns[category]+=elapsed-item[1];self.calls[category]+=1
                if self.stack:self.stack[-1][1]+=elapsed
                if category=='sql':self.queries.append(dict(sql=args[1],params=encode(args[2] if len(args)>2 else kwargs.get('subvals',()))))
        return wrapped


def main():
    out=ROOT/'evidence/beets-feasibility'/('20261108-'+uuid4().hex[:12]);out.mkdir(parents=True)
    checks=[];report=dict(status='running',checks=checks,source_commit=COMMIT,optimized=False,timing_comparison=False)
    def save(n,v):
        p=out/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
    def check(n,v):
        assert v,n
        checks.append(n)
    save('protocol.json',dict(sizes=[100,1000,5000],seed=20261108,
        workload='Original ExportPlugin.run corresponding to export --library JSON, all default fields, id+ order, no limit; 90% album tracks and 10% singletons',
        metadata='Full Item/Album schema; duplicate titles, Unicode, fixed added times, item and album flex attributes; real small valid WAV files retained',
        schedule=['one original warmup','two plain original runs','one nested-cost observer run','one CPU profile run'],
        semantics=['same Results instance identity and mutation','independent Results identities','computed getter effects and errors','slow Python predicate','external commit between main and flex reads'],
        failure_lessons={'alfio':'do not collapse repeated reads, locks or exception/control effects without evidence',
                         'oscar':'require a substantial attributable region; preserve data, materialization effects and identity; measure profiling perturbation'},
        stop='One fixed original-only screen; no candidate, extra timing, codec simplification, changed isolation or per-project optimizer',
        gates='Cost evidence, effect closure, and unchanged shared-algorithm/backend compatibility assessed separately; no statistical speedup claim'))
    os.environ['BEETSDIR']=str(out/'config');(out/'config').mkdir()
    (out/'config/config.yaml').write_text('plugins: []\n')
    sys.path.insert(0,str(SOURCE));os.environ['PYTHONDONTWRITEBYTECODE']='1'
    try:
        tree=load(TEMP/'beets-tree.json');source_manifest={}
        for entry in tree['tree']:
            if entry['type']!='blob':continue
            p=SOURCE/entry['path'];data=p.read_bytes()
            assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==entry['sha'],entry['path']
            source_manifest[entry['path']]=sha(p)
        check('complete source archive agrees with fixed Git tree',not tree['truncated'] and bool(source_manifest))
        save('source-manifest.json',source_manifest)
        shutil.copy2(TEMP/'beets-tree.json',out/'upstream-tree.json')
        shutil.copy2(TEMP/'beets-install.json',out/'install-report.json')
        (out/'packages.txt').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze','--all']))
        for name in ['beets/dbcore/db.py','beets/library/models.py','beetsplug/export.py']:
            py_compile.compile(str(SOURCE/name),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        import beets
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Library,Item
        from beetsplug import export,info
        beets.config['plugins']=[]
        plugin=export.ExportPlugin()
        def canonical(c):
            def val(v):
                if isinstance(v,CodeType):return canonical(v)
                if isinstance(v,tuple):return [val(x) for x in v]
                return [type(v).__name__,repr(v)]
            return dict(name=c.co_qualname,line=c.co_firstlineno,code=c.co_code.hex(),exceptions=c.co_exceptiontable.hex(),
                        names=c.co_names,varnames=c.co_varnames,constants=[val(v) for v in c.co_consts])
        def walk(c):
            yield c
            for x in c.co_consts:
                if isinstance(x,CodeType):yield from walk(x)
        witnesses=[]
        for fn in [core.Database._get_results,core.Results._get_objects,core.Results._make_model,export.ExportPlugin.run]:
            path=Path(fn.__code__.co_filename);raw=Path(importlib.util.cache_from_source(str(path))).read_bytes()
            check(fn.__qualname__+' checked-hash bytecode matches source',int.from_bytes(raw[4:8],'little')==3 and raw[8:16]==importlib.util.source_hash(path.read_bytes()))
            check(fn.__qualname__+' runtime matches pyc code',any(canonical(c)==canonical(fn.__code__) for c in walk(marshal.loads(raw[16:]))))
            witnesses.append(dict(function=fn.__qualname__,source=str(path),source_sha256=sha(path),pyc_sha256=hashlib.sha256(raw).hexdigest()))
        save('bytecode-witness.json',dict(witnesses=witnesses,scope='Controlled source-to-pyc witness, not independent decompilation or rebuilt equivalent program'))
        save('runtime.json',dict(python=sys.version,beets=beets.__version__,sqlite=sqlite3.sqlite_version,source=beets.__file__,plugins=['export'],
            archive_sha256=sha(TEMP/'beets-e978874d.tar.gz'),profile='CPU cProfile kept separate; nested observer intervals exclusive; plain runs unpatched'))
        def run_export(lib,target,args):
            opts=SimpleNamespace(output=str(target),append=False,format='json',library=True,album=False,included_keys=[],limit=None)
            plugin.run(lib,opts,args)
        summaries=[];libraries={}
        for size in [100,1000,5000]:
            folder=out/str(size);folder.mkdir();music=folder/'music';music.mkdir()
            path=folder/'library.db';lib=Library(path,directory=music);libraries[size]=(lib,path)
            print('Preparing complete original library',size,flush=True)
            with lib.transaction():
                group=[]
                for i in range(size):
                    audio=music/f'{i:06d}.wav'
                    with wave.open(str(audio),'wb') as stream:
                        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*80)
                    item=Item(path=str(audio),title=f'Track {i%37} café',artist=f'Artist {i%13}',album=f'Album {i//10}',
                              albumartist=f'Artist {i//10%13}',track=i%10+1,disc=1,year=2000+i%25,format='WAV',
                              length=.01,samplerate=8000,bitdepth=16,channels=1,added=1700000000+i,mtime=0)
                    item['probe_tag']='old';item['audit_note']=f'完整-{i%17}';item['rating_hint']=i%5
                    if i%10==9:
                        lib.add(item);album=lib.add_album(group);album.added=1700000000+i
                        album['collection_batch']=f'batch-{i//10%7}';album.store();group=[]
                    else:group.append(item)
            before=snapshot(path);save(f'{size}/before-state.json',before)
            check(f'{size} complete fixture counts',len(before['tables']['items'])==size and len(before['tables']['albums'])==size//10)
            modes=['warmup','plain-1','plain-2','observed','profile'];records=[];expected=None
            for mode in modes:
                target=folder/(mode+'.json');meter=Meter();prof=cProfile.Profile(timer=time.thread_time_ns,timeunit=1e-9)
                with ExitStack() as stack:
                    if mode=='observed':
                        for cls,name,cat in [(core.Transaction,'query','sql'),(core.Results,'_make_model','materialize'),
                                              (Item,'try_filesize','media'),(Item,'has_cover_art','media')]:
                            stack.enter_context(patch.object(cls,name,meter.wrap(cat,getattr(cls,name))))
                        emitter=info.library_data_emitter
                        def factory(model):return meter.wrap('field_emit',emitter(model))
                        stack.enter_context(patch.object(info,'library_data_emitter',factory))
                        stack.enter_context(patch.object(export.json,'dump',meter.wrap('serialize',export.json.dump)))
                    wall=time.perf_counter_ns();cpu=time.thread_time_ns()
                    if mode=='profile':prof.enable()
                    try:run_export(lib,target,['id+'])
                    finally:
                        if mode=='profile':prof.disable()
                    wall=time.perf_counter_ns()-wall;cpu=time.thread_time_ns()-cpu
                output=load(target);digest=sha(target)
                if expected is None:expected=digest
                check(f'{size}/{mode} full output stable',digest==expected and len(output)==size)
                check(f'{size}/{mode} all default and flex fields retained',all('audit_note' in x and 'probe_tag' in x and 'filesize' in x and 'has_cover_art' in x for x in output))
                record=dict(mode=mode,wall_ms=wall/1e6,cpu_ms=cpu/1e6,bytes=target.stat().st_size,output_sha256=digest)
                if mode=='observed':
                    record.update(exclusive_ms={k:v/1e6 for k,v in meter.ns.items()},calls=dict(meter.calls))
                    save(f'{size}/queries.json',meter.queries)
                    record['unassigned_ms']=wall/1e6-sum(meter.ns.values())/1e6
                if mode=='profile':
                    stats=[]
                    for entry in prof.getstats():
                        c=entry.code
                        stats.append(dict(name=c if isinstance(c,str) else c.co_qualname,file=None if isinstance(c,str) else c.co_filename,
                                          calls=entry.callcount,self_cpu_ms=entry.inlinetime*1000,inclusive_cpu_ms=entry.totaltime*1000))
                    save(f'{size}/cpu-profile.json',sorted(stats,key=lambda r:-r['self_cpu_ms']))
                records.append(record);save(f'{size}/runs.json',records)
                print(size,mode,'completed',flush=True)
            after=snapshot(path);save(f'{size}/after-state.json',after)
            check(f'{size} every schema table row unchanged',before==after)
            plain=statistics.median(r['wall_ms'] for r in records if r['mode'].startswith('plain'))
            observed=next(r for r in records if r['mode']=='observed');profiled=next(r for r in records if r['mode']=='profile')
            summaries.append(dict(size=size,plain_wall_ms=plain,observed_wall_factor=observed['wall_ms']/plain,
                                  profile_wall_factor=profiled['wall_ms']/plain,observed=observed))
            save('cost-summary.json',summaries)
        # Semantic witnesses on the small complete fixture; no optimized arm.
        lib,path=libraries[100];state=snapshot(path);semantic={}
        results=lib.items(['id+']);first=list(results);original_title=first[0].title;first[0].title='in-memory edit'
        again=list(results);independent=list(lib.items(['id+']))
        check('repeated Results keeps same instances and in-memory edits',all(a is b for a,b in zip(first,again)) and again[0].title=='in-memory edit')
        check('independent Results makes fresh objects from full data',all(a is not b for a,b in zip(first,independent)) and independent[0].title==original_title)
        semantic['identity']=dict(repeated_same_instances=True,separate_results_new_instances=True,in_memory_edit_visible_only_on_original_results=True)
        calls=[];getters=plugins.item_field_getters
        def effect(item):calls.append(item.id);return 'computed-'+str(item.id)
        with patch.object(plugins,'item_field_getters',lambda:{**getters(),'probe_effect':effect}):
            run_export(lib,out/'getter-effect.json',['id:1'])
        check('computed plugin field effect retained',calls==[1] and load(out/'getter-effect.json')[0]['probe_effect']=='computed-1')
        def fail(item):raise RuntimeError('synthetic computed field failure')
        error=None
        with patch.object(plugins,'item_field_getters',lambda:{**getters(),'probe_effect':fail}):
            try:run_export(lib,out/'getter-failure-output.json',['id:1'])
            except RuntimeError as exc:error=str(exc)
        check('computed field failure propagates before JSON output',error=='synthetic computed field failure')
        semantic['getter']=dict(calls=calls,error=error)
        class Slow(query.Query):
            def clause(self):return None,()
            def match(self,obj):slow_calls.append(obj.id);return obj.id%2==1
        slow_calls=[];slow=list(lib.items(Slow()))
        check('Python predicate observes complete candidate objects',len(slow_calls)==100 and {x.id for x in slow}==set(range(1,101,2)))
        semantic['slow_predicate']=dict(calls=len(slow_calls),returned=len(slow))
        # The application's transaction context does not itself issue BEGIN for
        # reads. Test the actual connection, never substitute another isolation.
        original_query=core.Transaction.query;interleaving=[]
        def query_hook(tx,sql,params=()):
            values=original_query(tx,sql,params)
            if not interleaving and 'items.*' in sql:
                interleaving.append(dict(in_transaction=tx.db._connection().in_transaction))
                with sqlite3.connect(path) as other:
                    other.execute('UPDATE items SET title=? WHERE id=1',('committed external title',))
                    other.execute("UPDATE item_attributes SET value=? WHERE entity_id=1 AND key='probe_tag'",('new',))
            return values
        old=independent[0].title
        try:
            with patch.object(core.Transaction,'query',query_hook):mixed=lib.items(['id:1']).get()
            check('external commit can split original fixed/flex read views',len(interleaving)==1 and mixed.title==old and mixed['probe_tag']=='new')
            semantic['read_boundary']=dict(fixed_title=mixed.title,flex_value=mixed['probe_tag'],external_fixed_title='committed external title',gate=interleaving)
        finally:
            with sqlite3.connect(path) as other:
                other.execute('UPDATE items SET title=? WHERE id=1',(old,))
                other.execute("UPDATE item_attributes SET value=? WHERE entity_id=1 AND key='probe_tag'",('old',))
        check('all semantic probes preserve complete database after restoration',snapshot(path)==state)
        save('semantic-witnesses.json',semantic)
        for library,_ in libraries.values():library._close()
        check('all upstream sources unchanged',all(sha(SOURCE/f)==h for f,h in source_manifest.items()))
        report.update(status='passed',sizes=[100,1000,5000],runs=15,summary=summaries,
            semantic_witnesses=semantic,limits=['No candidate or speedup comparison','Synthetic metadata and short valid WAVs, not representative production distribution',
                'Original export plugin entry called directly; CLI startup and argument parsing excluded',
                'Observed intervals are affected costs, not proved removable costs; observer and CPU profile perturbation reported',
                'Small read-boundary witness refutes universal single-snapshot fusion, not all restricted static-library transformations',
                'Same shared optimizer still requires a verified backend and compatible objective; none admitted here'])
    except BaseException as exc:
        report.update(status='failed',error=repr(exc));raise
    finally:
        save('run.json',report);shutil.copy2(__file__,out/'probe.py')
        save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(checks))),flush=True)


if __name__=='__main__':main()
