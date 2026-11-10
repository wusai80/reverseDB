#!/usr/bin/env python3
"""Fixed real-DB scalar-consumer contract; no fusion backend or timing."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from placement_frontend import select_complete_contract

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evidence/oscar-baseline/20261106-ed9f704d86d6'
SCREEN = ROOT / 'evidence/oscar-cross-layer-screen/20261107-c38bbd11addf'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'
PYTHON = ROOT / 'work/oscar/venv/bin/python'
DOCKER = ['docker', '--context', 'desktop-linux']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p):
    return json.loads(p.read_text())


def main():
    out = ROOT / 'evidence/oscar-analytics-contract' / ('20261107-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    resource_name = 'reversedb-oscar-analytics-' + out.name
    resources = []
    report = dict(status='running', checks=[], commands=[], inputs={}, cleanup_errors=[])
    def save(n, value):
        (out / n).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def check(n, ok):
        assert ok, n
        report['checks'].append(n)
    def run(cmd, log, stdin=None, env=None, required=True):
        cmd = list(map(str, cmd)); report['commands'].append(cmd)
        p = subprocess.run(cmd, cwd=ROOT, stdin=stdin, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=300)
        (out / log).write_bytes(p.stdout)
        if required and p.returncode:
            raise RuntimeError(str(out / log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q, log):
        return run(DOCKER + ['exec', resource_name, 'psql', '-U', 'postgres', '-d', 'postgres',
                            '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', q], log)
    try:
        save('protocol.json', dict(
            cases=['ordinary','empty','bag','wide-sum','existing-overflow','missing','missing-wide',
                   'miss-mutates-lines','read-interleaving','prefetched-lines','init-mutates','init-fails','update-check-fails'],
            arms=['original','candidate'], negative_controls=['miss-mutates-lines','read-interleaving'],
            permission_control='restricted-column role: full read/full-projection aggregate denied, narrow aggregate allowed',
            seed='one original four-order replay, then full database clones',
            compare='exact errors/logs/model events/results, all 97 tables and 94 sequences before each rollback',
            candidate='first num_items only; original separate count, UPDATE, miss re-read and CREATE retained',
            stop='fixed semantic experiment; no performance samples, fused backend, application install or optimizer change'))
        for folder, names in [(BASE, ['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/after-full-state.json','a/loaded-sources.json']),
                              (SCREEN, ['source-bytecode.json','prefix-source-witnesses.json','prefix-contract.json','prefix-selection.json'])]:
            manifest=load(folder/'manifest.json')
            for f in names:
                p=folder/f
                check('retained '+str(p.relative_to(ROOT)),sha(p)==manifest[f])
                report['inputs'][str(p)]=sha(p)
        for f,h in load(BASE/'source-manifest.json').items():
            assert sha(SOURCE/f)==h,f
            report['inputs'][str(SOURCE/f)]=h
        check('frozen application sources unchanged',True)
        for entry in load(BASE/'a/loaded-sources.json').values():
            p=Path(entry['path'])
            assert sha(p)==entry['sha256'],str(p)
            report['inputs'][str(p)]=entry['sha256']
        check('loaded framework and application sources frozen',True)
        first=load(FIRST/'manifest.json')
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            p=ROOT/'scripts'/f
            check('shared core unchanged '+f,sha(p)==first['inputs/scripts/'+f])
            report['inputs'][str(p)]=sha(p)
        for p in [Path(__file__).resolve(),ROOT/'scripts/placement_frontend.py',
                  ROOT/'prototype/oscar/analytics_contract_probe.py',ROOT/'prototype/oscar/baseline_probe.py']:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest);report['inputs'][str(p)]=sha(p)
        for f in ['fixture-request.json','fixture-full-state.json']:
            shutil.copy2(BASE/f,out/f)
        codes={x['name']:x['hash'] for x in load(SCREEN/'source-bytecode.json')}
        reads=[x['events'][1]['sql'] for x in load(SCREEN/'prefix-source-witnesses.json')]
        check('same original full projection in all four prefixes',len(reads)==4 and len(set(reads))==1)
        save('binding.json',dict(read_sql=reads[0],receiver_hash=codes['_record_user_order'],items_hash=codes['AbstractOrder.num_items']))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',TZ='UTC',
                 PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
                 DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',
                 DATABASE_HOST='127.0.0.1',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-analytics-only',
                 RDB_ANALYTICS_BINDING=str(out/'binding.json'),RDB_EXPECTED_STATE=str(BASE/'a/after-full-state.json'))
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres']
        check('pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-analytics-contract',resource_name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',resource_name,'--label','reversedb.experiment=oscar-analytics-contract',
                    '--memory=1024m','--cpus=2','--mount','type=volume,src='+resource_name+',dst=/var/lib/postgresql',
                    '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-analytics-admin',image['resolved'],
                    '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on',
                    '-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',resource_name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-analytics-only'",'role.log')
        sql("CREATE ROLE oscar_projection_reader WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-projection-only'",'reader-role.log')
        sql('CREATE DATABASE oscar_seed OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:
            run(DOCKER+['exec','-i',resource_name,'psql','-U','oscar_user','-d','oscar_seed','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',resource_name,'5432/tcp'],'port.log')
        assert host.startswith('127.0.0.1:') and '\n' not in host
        env['DATABASE_PORT']=host.split(':')[1]
        print('Seeding with original four-order replay',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/baseline_probe.py','replay',out/'seed'],'seed.log',env=dict(env,DATABASE_NAME='oscar_seed'))
        check('seed equals complete frozen post-order state',load(out/'seed/after-full-state.json')==load(BASE/'a/after-full-state.json'))
        for arm in ['original','candidate','naive']:
            db='oscar_contract_'+arm
            sql('CREATE DATABASE '+db+' WITH TEMPLATE oscar_seed OWNER oscar_user',db+'-create.log')
            print('Running '+arm,flush=True)
            run([PYTHON,ROOT/'prototype/oscar/analytics_contract_probe.py','contract',out/arm],arm+'.log',
                env=dict(env,DATABASE_NAME=db,RDB_ANALYTICS_ARM=arm))
        a=load(out/'original/cases.json');b=load(out/'candidate/cases.json')
        check('all 13 fixed cases executed',len(a)==len(b)==13 and [x['case'] for x in a]==load(out/'protocol.json')['cases'])
        effects=['case','error','rollback_flag','logs','model_events','user_records','prefetched_alias_preserved','interleaved']
        for x,y in zip(a,b):
            label=x['case']
            check(label+' exact errors/results/effects/identity',all(x[k]==y[k] for k in effects))
            check(label+' complete tables and sequences',load(out/'original'/(label+'-full-state.json'))==load(out/'candidate'/(label+'-full-state.json')))
            if label in ['missing','missing-wide','miss-mutates-lines']:
                check(label+' original second materialization retained',y['stats']['items_calls']==2 and y['stats']['aggregates']==1 and
                      sum(e['sql']==reads[0] for e in y['sql'])==1)
            # Apart from the eligible initial SELECT, original host continuation SQL and params must agree.
            xs=[e for e in x['sql'] if e['sql'].startswith(('SELECT','INSERT','UPDATE','DELETE'))]
            ys=[e for e in y['sql'] if e['sql'].startswith(('SELECT','INSERT','UPDATE','DELETE'))]
            normalized=copy.deepcopy(ys)
            aggregate=load(out/'candidate/binding.json')['aggregate_sql']
            for e in normalized:
                if e['sql']==aggregate:e['sql']=reads[0]
            check(label+' original statement order and continuation parameters',xs==normalized)
        bycase={x['case']:x for x in b}
        for label,value in [('ordinary',6),('empty',0),('bag',4),('prefetched-lines',14),('init-mutates',48),('missing',6)]:
            check(label+' expected integer result',bycase[label]['user_records'][0]['num_order_items']==value)
        for label in ['wide-sum','existing-overflow','missing-wide']:
            check(label+' original overflow error preserved',bycase[label]['error']['type']=='DataError' and bycase[label]['error']['sqlstate']=='22003')
        check('wide intermediate remains exact Python integer',bycase['wide-sum']['aggregate_values'][0]['host_value']==4294967294)
        check('missing wide reaches original pre-save before failure',[e['kind'] for e in bycase['missing-wide']['model_events']]==['user_post_init','user_pre_save'])
        check('caught IntegrityError preserves log and rollback',bycase['update-check-fails']['error'] is None and
              bycase['update-check-fails']['rollback_flag'] is True and len(bycase['update-check-fails']['logs'])==1)
        controls=[]
        for bad in load(out/'naive/cases.json'):
            label=bad['case'];good=bycase[label]
            check(label+' unsafe alternative produces counterexample',good['user_records'][0]['num_order_items']==12 and bad['user_records'][0]['num_order_items']==6)
            goodstate=load(out/'candidate'/(label+'-full-state.json'));badstate=load(out/'naive'/(label+'-full-state.json'))
            adjusted=copy.deepcopy(badstate)
            for row in adjusted['tables']['analytics_userrecord']:
                if row['user_id']==1:row['num_order_items']=12
            check(label+' exact state difference isolated to wrong statistic',goodstate!=badstate and adjusted==goodstate)
            controls.append(dict(case=label,original_and_candidate=12,unsafe=6))
        selection=select_complete_contract(load(SCREEN/'prefix-contract.json'))
        save('selection.json',selection)
        check('same DP still retains original prefix',selection==load(SCREEN/'prefix-selection.json') and selection['selection']['objective']==[3,0,0])
        check('all retained sources unchanged',all(sha(Path(p))==h for p,h in report['inputs'].items()))
        save('comparison.json',dict(cases=13,negative_controls=controls,
             original_runtime_checks=len(load(out/'original/result.json')['checks']),
             candidate_runtime_checks=len(load(out/'candidate/result.json')['checks']),
             naive_runtime_checks=len(load(out/'naive/result.json')['checks']),
             seed_checks=len(load(out/'seed/result.json')['checks']),
             full_tables=97,sequences=94,remaining_calls=3,performance_claim=None,
             conclusion='Bounded first-read relationalization passed; snapshot fusion and stale miss-summary reuse rejected',
             admission='No general backend admission; frozen source/schema/config, original effects and unknown boundaries retained'))
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
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
