#!/usr/bin/env python3
"""One fixed original/candidate four-order integration differential, no timing."""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4
from test_oscar_analytics_contract import ROOT,BASE,SCREEN,FIRST,SOURCE,PYTHON,DOCKER,sha,load
from placement_frontend import select_complete_contract

CONTRACT=ROOT/'evidence/oscar-analytics-contract/20261107-b101e5bf5f9e'
ORDERS=['RDB-ONE','RDB-THREE','RDB-SIX','RDB-DIGITAL']


def main():
    out=ROOT/'evidence/oscar-analytics-replay'/('20261107-'+uuid4().hex[:12]);out.mkdir(parents=True)
    resource_name='reversedb-oscar-analytics-replay-'+out.name
    resources=[];report=dict(status='running',checks=[],commands=[],inputs={},cleanup_errors=[])
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
    def check(n,v):
        assert v,n
        report['checks'].append(n)
    def run(cmd,log,stdin=None,env=None,required=True):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,stdin=stdin,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
        (out/log).write_bytes(p.stdout)
        if required and p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q,log):return run(DOCKER+['exec',resource_name,'psql','-U','postgres','-d','postgres','-XAt','-v','ON_ERROR_STOP=1','-c',q],log)
    try:
        save('protocol.json',dict(orders=ORDERS,arms=['original','candidate'],repetitions=1,
            scope='Direct original OrderCreator with sandbox request-equivalent atomic scope and actual commits; not HTTP checkout',
            candidate='Only original receiver first num_items LOAD_ATTR; no snapshot merging or miss-summary reuse',
            compare='Every committed full state, final full state, order outputs, retained Python event/identity graph, all SQL except four explicit aggregate substitutions',
            stop='No performance measurement, additional workload, first-project algorithm change or default installation'))
        for folder,names in [(BASE,['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/loaded-sources.json','a/after-full-state.json','a/result.json']),
                             (CONTRACT,['binding.json','comparison.json','inputs/prototype/oscar/analytics_contract_probe.py']),
                             (SCREEN,['prefix-contract.json','prefix-selection.json'])]:
            m=load(folder/'manifest.json')
            for f in names:
                p=folder/f;check('retained '+str(p.relative_to(ROOT)),sha(p)==m[f]);report['inputs'][str(p)]=sha(p)
        for f,h in load(BASE/'source-manifest.json').items():
            p=SOURCE/f;assert sha(p)==h,f;report['inputs'][str(p)]=h
        for e in load(BASE/'a/loaded-sources.json').values():
            p=Path(e['path']);assert sha(p)==e['sha256'],str(p);report['inputs'][str(p)]=e['sha256']
        check('frozen application framework and driver sources unchanged',True)
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            p=ROOT/'scripts'/f;check('shared core unchanged '+f,sha(p)==load(FIRST/'manifest.json')['inputs/scripts/'+f]);report['inputs'][str(p)]=sha(p)
        for p in [Path(__file__).resolve(),ROOT/'scripts/test_oscar_analytics_contract.py',ROOT/'scripts/placement_frontend.py']+[
            ROOT/'prototype/oscar'/f for f in ['baseline_probe.py','analytics_relationalization.py','analytics_replay_probe.py']]:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);report['inputs'][str(p)]=sha(p)
        # Mechanical link to the passing local contract: exact guard AST and
        # numeric execution/conversion AST, not a fresh per-project heuristic.
        old=ast.parse((CONTRACT/'inputs/prototype/oscar/analytics_contract_probe.py').read_text())
        new=ast.parse((ROOT/'prototype/oscar/analytics_relationalization.py').read_text())
        def function(tree,name):return next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
        check('guard exactly matches passing contract AST',ast.dump(function(old,'guard'))==ast.dump(function(new,'guard')))
        def aggregate_block(tree):
            body=function(tree,'items').body
            start=next(i for i,n in enumerate(body) if isinstance(n,ast.With))
            return [ast.dump(n) for n in body[start:start+3]]
        check('aggregate execution and exact integer conversion match passing AST',aggregate_block(old)==aggregate_block(new))
        binding=load(CONTRACT/'binding.json');save('binding.json',binding)
        for f in ['fixture-request.json','fixture-full-state.json']:shutil.copy2(BASE/f,out/f)
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',TZ='UTC',
            PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),DJANGO_SETTINGS_MODULE='settings',
            DATABASE_ENGINE='django.db.backends.postgresql',DATABASE_HOST='127.0.0.1',DATABASE_USER='oscar_user',
            DATABASE_PASSWORD='synthetic-analytics-replay-only',RDB_ANALYTICS_BINDING=str(out/'binding.json'))
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres']
        check('pinned image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-analytics-replay',resource_name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',resource_name,'--label','reversedb.experiment=oscar-analytics-replay',
            '--memory=1024m','--cpus=2','--mount','type=volume,src='+resource_name+',dst=/var/lib/postgresql',
            '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-analytics-replay-admin',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',resource_name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-analytics-replay-only'",'role.log')
        sql('CREATE DATABASE oscar_fixture OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:
            run(DOCKER+['exec','-i',resource_name,'psql','-U','oscar_user','-d','oscar_fixture','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',resource_name,'5432/tcp'],'port.log');assert host.startswith('127.0.0.1:') and '\n' not in host
        env['DATABASE_PORT']=host.split(':')[1]
        for arm in ['original','candidate']:
            db='oscar_replay_'+arm;sql('CREATE DATABASE '+db+' WITH TEMPLATE oscar_fixture OWNER oscar_user',db+'-create.log')
            print('Running fixed four-order '+arm,flush=True)
            run([PYTHON,ROOT/'prototype/oscar/analytics_replay_probe.py','replay',out/arm],arm+'.log',
                env=dict(env,DATABASE_NAME=db,RDB_ANALYTICS_ARM=arm))
        a=load(out/'original/relationalization.json');b=load(out/'candidate/relationalization.json')
        aggregate=b['specialization']['aggregate_sql'];identity_map={};reverse_map={};retained_ids=set()
        def compare_event(x,y):
            x=copy.deepcopy(x);y=copy.deepcopy(y)
            for k in ['identity','return_identity']:
                if k in x:
                    if k not in y:return False
                    oldid=x.pop(k);newid=y.pop(k);retained_ids.add(oldid)
                    if oldid in identity_map and identity_map[oldid]!=newid:return False
                    if newid in reverse_map and reverse_map[newid]!=oldid:return False
                    identity_map[oldid]=newid;reverse_map[newid]=oldid
            return x==y
        counts=[]
        for number in ORDERS:
            x=load(out/'original'/(number+'-trace.json'));y=load(out/'candidate'/(number+'-trace.json'))
            sx=[e for e in x if e['kind'] in ['execute','executemany']];sy=[e for e in y if e['kind'] in ['execute','executemany']]
            check(number+' same physical call count',len(sx)==len(sy))
            replacements=0
            for ix,(raw_left,raw_right) in enumerate(zip(sx,sy)):
                left={k:v for k,v in raw_left.items() if k!='stack'};right={k:v for k,v in raw_right.items() if k!='stack'}
                if right['sql']==aggregate:
                    check(number+' aggregate is original first-read replacement',left['sql']==binding['read_sql'] and right['rowcount']==1 and right['status']=='SELECT 1')
                    start,stop=b['specialization']['site']
                    check(number+' both reads bound to original initial bytecode site',all(any(
                        f['code']==binding['receiver_hash'] and start<=f['offset']<stop for f in e['stack']) for e in [raw_left,raw_right]))
                    right.update(sql=left['sql'],rowcount=left['rowcount'],status=left['status']);replacements+=1
                check(number+' SQL parameters/status/transaction order '+str(ix),left==right)
            check(number+' exactly one aggregate substitution',replacements==1)
            px=[e for e in x if e['kind'].startswith('python_') and not e.get('initial_items_materialization')]
            py=[e for e in y if e['kind'].startswith('python_')]
            check(number+' retained Python effects and identity graph',len(px)==len(py) and all(compare_event(l,r) for l,r in zip(px,py)))
            check(number+' commit/rollback sequence preserved',[e['kind'] for e in x if e['kind'] in ['commit','rollback']]==[e['kind'] for e in y if e['kind'] in ['commit','rollback']]==['commit'])
            check(number+' full committed state identical',load(out/'original'/(number+'-committed-state.json'))==load(out/'candidate'/(number+'-committed-state.json')))
            counts.append(dict(order=number,calls=len(sx),removed_models=sum(r['order_id']==ORDERS.index(number)+1 for r in a['initial_materializations'])))
        ar=load(out/'original/result.json');br=load(out/'candidate/result.json')
        check('instrumented original results equal frozen baseline',ar['orders']==load(BASE/'a/result.json')['orders'])
        for x,y in zip(ar['orders'],br['orders']):
            check(y['number']+' stock result references extend the same bijection',
                  len(x['stock_identities'])==len(y['stock_identities']) and all(
                      compare_event(dict(identity=oldid),dict(identity=newid))
                      for oldid,newid in zip(x['stock_identities'],y['stock_identities'])))
            x=copy.deepcopy(x);x['stock_identities']=[identity_map[i] for i in x['stock_identities']]
            check(y['number']+' all order outputs and stock aliases identical',x==y)
        check('eliminated objects never escape into retained identity graph',not ({r['identity'] for r in a['initial_materializations']} & retained_ids))
        check('complete final tables and sequences equal frozen baseline',load(out/'original/after-full-state.json')==load(out/'candidate/after-full-state.json')==load(BASE/'a/after-full-state.json'))
        check('eleven fewer initial Line constructions',a['counts']['init']-b['counts']['init']==a['counts']['from_db']-b['counts']['from_db']==11)
        for k,v in a['counts_by_model'].items():
            check('only order.Line construction reduced '+k,v-b['counts_by_model'].get(k,0)==(11 if k.endswith(':order.Line') else 0))
        check('four exact integer summaries',[(r['order_id'],r['value']) for r in b['specialization']['reads']]==[(1,1),(2,3),(3,6),(4,1)])
        for label in ['empty','duplicate']:
            x=load(out/'original'/(label+'-trace.json'));y=load(out/'candidate'/(label+'-trace.json'))
            check(label+' error-prefix SQL and transaction outcomes unchanged',[{k:v for k,v in e.items() if k!='stack'} for e in x]==[{k:v for k,v in e.items() if k!='stack'} for e in y])
        selection=select_complete_contract(load(SCREEN/'prefix-contract.json'));save('selection.json',selection)
        check('unchanged original DP retains prefix',selection==load(SCREEN/'prefix-selection.json'))
        check('all frozen inputs unchanged',all(sha(Path(p))==h for p,h in report['inputs'].items()))
        save('comparison.json',dict(orders=counts,original_counts=a['counts'],candidate_counts=b['counts'],
            calls=sum(r['calls'] for r in counts),model_identity_bijection=identity_map,
            runtime_checks=[len(ar['checks']),len(br['checks'])],probe_checks=[len(a['checks']),len(b['checks'])],
            result='bounded integration passed',performance_claim=None,default_application_modified=False,
            limits=['No HTTP coverage, general backend admission or timing','187 calls retained; four scalar reads replace eleven complete row transfers','Identity labels compared by injective correspondence, not discarded']))
        report.update(status='passed',performance_claim=None,optimizer_modified=False,application_installed=False)
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        for resource in reversed(resources):
            try:
                if resource=='container':
                    run(DOCKER+['logs',resource_name],'postgres.log',required=False)
                    run(DOCKER+['rm','-f','-v',resource_name],'cleanup-container.log')
                else:run(DOCKER+['volume','rm',resource_name],'cleanup-volume.log')
            except Exception as error:report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:report['status']='failed'
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
