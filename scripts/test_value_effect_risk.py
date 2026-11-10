#!/usr/bin/env python3
"""Fixed real SQL-candidate migration: fit sources, freeze, execute targets."""
import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from test_name_lookup_pruning import TABLES, SCENARIOS
from value_effect_delta import FEATURES, vector, controls
sys.path.insert(0,str(ROOT/'prototype/module_pruning'))
from interaction_risk_pilot import fit, predict


def rewrite(source, method, old, new):
    if old is None:return source
    matches=list(re.finditer(r'\b(?:private|public)\s+[^\n]+\s'+re.escape(method)+r'\([^\n]+\)\s+throws[^\n]+\{',source))
    assert len(matches)==1,method
    match=re.search(r'getPreparedStatement\(conn, this\.(\w+)\)',source[matches[0].end():]);assert match
    field=match[1];start=source.index('public SQLStmt '+field+' ');end=source.index(';',start)+1
    text=source[start:end];assert text.count(old)==1,(field,old)
    return source[:start]+text.replace(old,new)+source[end:]


def canonical_bindings(facts):
    return [dict(owner=b['owner'],operation=b['operation'],arguments=b['arguments'],guards=b['guards'],
                 receiver=b['receiver'].split('@')[0],receiver_stable=b['receiver_stable'],target=b['target']) for b in facts['bindings']]


def sql_by_method(facts):
    result={}
    for s in facts['statements']:
        key=s['receiver'].split('@')[0];assert key not in result and len(s['sql_alternatives'])==1
        result[key]=s['sql_alternatives'][0]
    return result


def main():
    protocol_path=ROOT/'prototype/module_pruning/value_effect_risk_protocol.json';protocol=json.loads(protocol_path.read_text())
    out=ROOT/'evidence/value-effect-risk'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    name='reversedb-virisk-'+out.name;resources=[]
    report=dict(status='running',inputs={},retained_assets={},commands=[],cases={},cleanup_errors=[],model_fits=0)
    env=dict(os.environ,TPCC_PASSWORD='synthetic-tpcc-only',TPCC_OBSERVE='0')
    def save(path,obj):(out/path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dest)
    def retain(archive,relative):
        path=archive/relative;assert digest(path)==json.loads((archive/'manifest.json').read_text())[relative]
        report['retained_assets'][str(path)]=digest(path);return path
    def run(cmd,log,stdin=None,check=True,timeout=180):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,env=env,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        (out/log).write_bytes(p.stdout)
        if check and p.returncode:raise RuntimeError('Command failed: '+str(out/log))
        return p.returncode,p.stdout.decode()
    def sql(query,log,db='experiment'):
        return run(DOCKER+['exec',name,'psql','-U','tpcc','-d',db,'-XAt','-v','ON_ERROR_STOP=1','-c',query],log)[1].strip()
    def state(folder,phase):
        result={}
        for table in TABLES:
            query=f"""SELECT json_build_object('rows',count(*),'sha256',encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex')) FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) x"""
            result[table]=json.loads(sql(query,folder+'/'+phase+'-'+table+'.log'))
        save(folder+'/'+phase+'-state.json',result);return result
    try:
        for path in [Path(__file__).resolve(),protocol_path,*[ROOT/'scripts'/n for n in ('value_effect_delta.py','value_effect_ir.py','jdbc_boundary_contracts.py','cross_layer_dependencies.py','app_db_smoke.py','test_name_lookup_pruning.py')],
                     ROOT/'prototype/module_pruning/interaction_risk_pilot.py',ROOT/'prototype/module_pruning/shared_risk.py',
                     ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java',ROOT/'app/tpcc/src/reversedb/tpcc/JdbcTrace.java',
                     ROOT/'app/tpcc/test/TraceClockBoundaryTest.java',ROOT/'work/paperless/images.lock.json']:
            freeze(path)
        save('protocol.json',protocol);save('feature-controls.json',controls());assert FEATURES==protocol['features']
        bundle=ARCHIVE/'bundle';libs=sorted(LIB.glob('*.jar'))
        retained=json.loads((ARCHIVE/'retained-assets.json').read_text());dump=ROOT/'work/tpcc/runs/c615d3357989/initial.dump'
        for p in [dump,*libs]:assert digest(p)==retained[str(p)];report['retained_assets'][str(p)]=digest(p)
        for p in [bundle/'reference.jar',*sorted((bundle/'reference-adapter').rglob('*.class')),*sorted((bundle/'candidate-classes').rglob('*.class'))]:retain(ARCHIVE,str(p.relative_to(ARCHIVE)))
        invariant=retain(ARCHIVE,'run/inputs/prototype/tpcc/invariants.sql')
        scenarios={n:retain(ARCHIVE,'run/inputs/app/tpcc/scenarios/'+n+'.json') for n in protocol['scenarios']}
        fixture=retain(ARCHIVE,'run/inputs/app/tpcc/scenarios/delivery_edges.sql')
        cp=':'.join(map(str,[bundle/'reference.jar',bundle/'candidate-classes',*libs]));jsonjar=LIB/'json-20240303.jar'
        extractor=out/'extractor';extractor.mkdir();runtime=out/'runtime';runtime.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',extractor,ROOT/'prototype/module_pruning/JdbcBoundaryExtract.java'],'compile-extractor.log')
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',runtime,
             ROOT/'app/tpcc/src/reversedb/tpcc/JdbcTrace.java',ROOT/'app/tpcc/test/TraceClockBoundaryTest.java'],'compile-tracer.log')
        run([JDK/'java','-cp',str(runtime)+':'+cp,'reversedb.tpcc.TraceClockBoundaryTest'],'trace-controls.log')
        base={};sources={}
        def extract(path,label):
            run([JDK/'java','-cp',str(extractor)+':'+str(jsonjar),'research.JdbcBoundaryExtract',out/(label+'.json'),cp,path],label+'.log')
            return json.loads((out/(label+'.json')).read_text())
        for module in protocol['development_modules']+protocol['target_modules']:
            path=retain(ARCHIVE,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+module+'.java')
            sources[module]=path.read_text();base[module]=extract(path,'base-'+module)
        candidates={};classes={}
        for plan in protocol['candidate_plan']:
            arm=plan['id'];module=plan['module'];folder=out/'generated'/arm;folder.mkdir(parents=True)
            source=rewrite(sources[module],plan['method'],plan['old'],plan['new']);path=folder/(module+'.java');path.write_text(source)
            facts=extract(path,'facts-'+arm);assert canonical_bindings(facts)==canonical_bindings(base[module]),arm
            original_sql=sql_by_method(base[module]);changed_sql=sql_by_method(facts)
            selected=[k for k in original_sql if '#'+plan['method']+'(' in k];assert len(selected)==1;selected=selected[0]
            assert original_sql.keys()==changed_sql.keys()
            assert all(original_sql[k]==changed_sql[k] for k in original_sql if k!=selected)
            a,b=original_sql[selected],changed_sql[selected]
            assert b==(a if plan['old'] is None else a.replace(plan['old'],plan['new']))
            result=vector(a,b);candidates[arm]=dict(plan,reference_sql=a,candidate_sql=b,source_sha256=digest(path),**result)
            target=out/(arm+'-classes');target.mkdir();classes[arm]=target
            run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',target,path],'compile-'+arm+'.log')
        save('preexecution-features.json',candidates)
        frozen={**report['inputs'],**{str(p.relative_to(ROOT)):digest(p) for p in (out/'generated').rglob('*.java')}}
        save('before-execution-freeze.json',dict(hashes=frozen,candidates=candidates,model_settings=protocol['model']))
        image=json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']
        assert run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')[1].strip()==image['id']
        run(DOCKER+['volume','create','--label','reversedb.experiment=value-effect-risk',name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=value-effect-risk','--memory=1536m','--cpus=2',
             '--mount','type=volume,src='+name+',dst=/var/lib/postgresql','-p','127.0.0.1::5432','-e','POSTGRES_USER=tpcc','-e','POSTGRES_PASSWORD=synthetic-tpcc-only',
             image['resolved'],'-c','timezone=UTC','-c','shared_buffers=256MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-U','tpcc'],f'ready-{i}.log',check=False)[1]:break
            time.sleep(.5)
        else:raise RuntimeError('readiness timeout')
        port=run(DOCKER+['port',name,'5432/tcp'],'port.log')[1].strip();assert port.startswith('127.0.0.1:') and '\n' not in port
        url='jdbc:postgresql://'+port+'/experiment?sslmode=disable&reWriteBatchedInserts=true'
        labels={}
        def replay(arm):
            candidate=candidates.get(arm);arm_cases=[]
            for scenario,path in scenarios.items():
                folder=scenario+'_'+arm;(out/folder).mkdir();inputs=json.loads(path.read_text())['commands'];byid={c['id']:c for c in inputs}
                sql('CREATE DATABASE experiment',folder+'/create.log','tpcc')
                with dump.open('rb') as stream:run(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','tpcc','-d','experiment'],folder+'/restore.log',stdin=stream)
                if scenario=='delivery_edges':sql(fixture.read_text(),folder+'/fixture.log')
                state(folder,'initial')
                gate=json.loads(sql("SELECT json_build_object('fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'),'timezone',current_setting('timezone'))",folder+'/gate.log'))
                assert gate==dict(fsync='on',synchronous_commit='on',full_page_writes='on',timezone='UTC')
                selected=([classes[arm]] if candidate else [])+[bundle/'reference-adapter',runtime]
                exit_code,output=run([JDK/'java','-Duser.timezone=UTC','-Xmx512m','-XX:ActiveProcessorCount=2',f'-Xlog:class+load=info:file={out/folder/"class-load.log"}',
                     '-cp',':'.join(map(str,selected))+':'+cp,'com.oltpbenchmark.api.TpccApp',url,'tpcc',path,out/folder,'reference'],folder+'/app.log',check=False)
                state(folder,'final');inv=json.loads(sql(invariant.read_text(),folder+'/invariants.log'))
                responses=json.loads((out/folder/'responses.json').read_text());trace=json.loads((out/folder/'jdbc-trace.json').read_text())
                assert [r['id'] for r in responses]==[c['id'] for c in inputs[:len(responses)]]
                counts=collections.Counter(r.get('status','failed') for r in responses)
                if not exit_code:assert len(responses)==len(inputs) and tuple(counts[s] for s in ('committed','aborted','rejected'))==SCENARIOS[scenario]
                else:assert responses and 'status' not in responses[-1]
                observed={r['id']:r for r in responses};executions=0
                for row in trace:
                    r=observed[row['id']]
                    assert [e['op'] for e in row['events'] if e['op'] in ('commit','rollback')]==['commit' if r.get('status')=='committed' else 'rollback']
                    if candidate:
                        executions+=sum(e['op'].startswith('execute') and e.get('sql')==' '.join(candidate['candidate_sql'].split()) for e in row['events'])
                loads=(out/folder/'class-load.log').read_text()
                assert str(runtime) in next(x for x in loads.splitlines() if 'reversedb.tpcc.JdbcTrace source:' in x)
                if candidate:
                    loaded=next(x for x in loads.splitlines() if 'com.oltpbenchmark.benchmarks.tpcc.procedures.'+candidate['module']+' source:' in x)
                    assert str(classes[arm]) in loaded
                comparisons={}
                for file in ('responses.json','observations.json','initial-state.json','final-state.json'):
                    actual=json.loads((out/folder/file).read_text())
                    if candidate:
                        reference=json.loads((out/(scenario+'_original')/file).read_text())
                        comparisons[file]=dict(equal=actual==reference,complete_replay_comparable=not exit_code or file=='initial-state.json')
                        if isinstance(actual,dict):comparisons[file]['different_keys']=[k for k in actual.keys()|reference.keys() if actual.get(k)!=reference.get(k)]
                        else:comparisons[file].update(different_indices=[i for i,(a,b) in enumerate(zip(actual,reference)) if a!=b],lengths=[len(reference),len(actual)])
                        if file=='initial-state.json':assert actual==reference
                    else:
                        old=retain(ROOT/'evidence/module-name-pruning/20261105-b14245195231',scenario+'_original/'+file)
                        assert actual==json.loads(old.read_text()),('baseline drift',scenario,file)
                if not candidate:assert not exit_code and not any(inv.values())
                save(folder+'/comparison.json',comparisons)
                case=dict(process_exit_code=exit_code,attempted=len(responses),completed=sum(v for k,v in counts.items() if k!='failed'),
                          counts=dict(counts),unexecuted_ids=[c['id'] for c in inputs[len(responses):]],invariants=inv,
                          selected_sql_executions=executions,comparisons=comparisons)
                report['cases'][folder]=case;arm_cases.append(case)
                sql('DROP DATABASE experiment',folder+'/drop.log','tpcc');print('Completed '+folder,flush=True)
            if candidate:
                assert sum(c['selected_sql_executions'] for c in arm_cases)>0,('candidate not exercised',arm)
                labels[arm]=dict(y=int(any(c['process_exit_code'] or any(not v['equal'] for v in c['comparisons'].values()) for c in arm_cases)),
                                 label_scope='fixed 34-command protocol only',all_inputs_equivalent=None)
                save('labels-so-far.json',labels)
        replay('original')
        development=[arm for arm,c in candidates.items() if c['module'] in protocol['development_modules']]
        targets=[arm for arm in candidates if arm not in development]
        for arm in development:replay(arm)
        rows=[dict(id=arm,family=candidates[arm]['module'],features=candidates[arm]['features'],y=labels[arm]['y']) for arm in development]
        assert all(r['features'] is not None for r in rows)
        model=fit(rows,FEATURES,1.0);report['model_fits']=1
        values={f:sorted({r['features'][f] for r in rows}) for f in FEATURES}
        predictions={arm:dict(status=candidates[arm]['status'],score=predict(model,candidates[arm]['features']) if candidates[arm]['features'] is not None else None,
                             prior=model['prior'],unseen_features=[f for f in FEATURES if candidates[arm]['features'][f] not in values[f]] if candidates[arm]['features'] else None) for arm in targets}
        save('before-target-predictions.json',dict(model=model,training_rows=rows,training_values=values,predictions=predictions,
             target_labels_opened=False,threshold=.5,feature_hash=digest(out/'preexecution-features.json')))
        prediction_hash=digest(out/'before-target-predictions.json')
        print('Target predictions frozen: '+json.dumps(predictions),flush=True)
        for arm in targets:replay(arm)
        assert digest(out/'before-target-predictions.json')==prediction_hash
        scored=[dict(id=arm,y=labels[arm]['y'],**p) for arm,p in predictions.items() if p['score'] is not None]
        def metrics(key):
            return dict(brier=sum((r[key]-r['y'])**2 for r in scored)/len(scored),
                        log_loss=-sum(r['y']*math.log(r[key])+(1-r['y'])*math.log1p(-r[key]) for r in scored)/len(scored),
                        false_negative=sum(r['y']==1 and r[key]<.5 for r in scored),false_positive=sum(r['y']==0 and r[key]>=.5 for r in scored))
        evaluation=dict(scored_candidates=len(scored),target_candidates=len(targets),abstentions=[a for a in targets if predictions[a]['score'] is None],
                        predictions=scored,metrics=dict(logistic=metrics('score'),training_prior=metrics('prior')),
                        all_labels=labels,training_constant_features=[f for f in FEATURES if len(values[f])==1],calibrated=False)
        save('evaluation.json',evaluation)
        for path,sha in frozen.items():assert digest(ROOT/path)==sha
        for path,sha in report['retained_assets'].items():assert digest(Path(path))==sha
        report.update(status='completed',scheduled=protocol['maximum_scheduled_requests'],attempted=sum(c['attempted'] for c in report['cases'].values()),
                      completed=sum(c['completed'] for c in report['cases'].values()),prediction_hash=prediction_hash,evaluation=evaluation)
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container':run(DOCKER+['logs',name],'postgres.log',check=False);run(DOCKER+['rm','-f','-v',name],'cleanup-container.log')
                else:run(DOCKER+['volume','rm',name],'cleanup-volume.log')
            except Exception as error:report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:report['status']='failed'
        save('report.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out))),flush=True)


if __name__=='__main__':main()
