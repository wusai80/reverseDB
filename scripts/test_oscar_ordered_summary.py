#!/usr/bin/env python3
"""Fixed ordered read-pair mechanism; no application replacement or timing."""
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
    out = ROOT / 'evidence/oscar-ordered-summary' / ('20261108-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    resource_name = 'reversedb-oscar-summary-' + out.name
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
        save('protocol.json',dict(cases=['ordinary','empty','bag','wide-sum','committed-update-between-reads',
            'committed-insert-between-reads','repeatable-read-control','fresh-after-zero-update','rls-invoker','projection-denied','count-denied'],
            arms=['original full-row/Python sum','ordered VOLATILE pair'],negative_control='STABLE version under two committed interleavings',
            coordination='Test-only advisory lock between count and sum, release after observed pg_locks wait and external commit',
            scope='Only read-pair SQL mechanism; original UPDATE/CREATE/callbacks not moved; no handoff or performance claim',
            failure_policy='Preserve full error diagnostics including server context differences; no retry/normalization',
            stop='fixed 11 cases / 24 arm runs once; no integration or timing'))
        for folder, names in [(BASE, ['fixture.sql','fixture-request.json','fixture-full-state.json','source-manifest.json','a/after-full-state.json','a/loaded-sources.json']+
                              ['a/'+n+'-trace.json' for n in ['RDB-ONE','RDB-THREE','RDB-SIX','RDB-DIGITAL']]),
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
                  ROOT/'prototype/oscar/ordered_summary_probe.py',ROOT/'prototype/oscar/baseline_probe.py']:
            dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest);report['inputs'][str(p)]=sha(p)
        for f in ['fixture-request.json','fixture-full-state.json']:
            shutil.copy2(BASE/f,out/f)
        codes={x['name']:x['hash'] for x in load(SCREEN/'source-bytecode.json')}
        reads=[x['events'][1]['sql'] for x in load(SCREEN/'prefix-source-witnesses.json')]
        check('same original full projection in all four prefixes',len(reads)==4 and len(set(reads))==1)
        count_sql=load(SCREEN/'prefix-source-witnesses.json')[0]['events'][0]['sql']
        count_sites=[]
        for number in ['RDB-ONE','RDB-THREE','RDB-SIX','RDB-DIGITAL']:
            for e in load(BASE/'a'/(number+'-trace.json')):
                if e['kind']=='execute' and e['sql']==count_sql:
                    for f in e['stack']:
                        if f['code']==codes['_record_user_order']:count_sites.append(dict(order=number,offset=f['offset']))
        check('six paired read positions include two original miss continuations',len(count_sites)==6 and sum(s['offset']>=348 for s in count_sites)==2)
        save('opportunity.json',dict(sites=count_sites,hypothetical_calls_saved=6,fraction_of_187=6/187,
            scope='SQL mechanism only; both original reads on miss would require a fresh packet, never cached values'))
        save('binding.json',dict(read_sql=reads[0],count_sql=count_sql,receiver_hash=codes['_record_user_order'],items_hash=codes['AbstractOrder.num_items']))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',TZ='UTC',
                 PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src',SOURCE/'sandbox'])),
                 DJANGO_SETTINGS_MODULE='settings',DATABASE_ENGINE='django.db.backends.postgresql',
                 DATABASE_HOST='127.0.0.1',DATABASE_USER='oscar_user',DATABASE_PASSWORD='synthetic-analytics-only',
                 RDB_ANALYTICS_BINDING=str(out/'binding.json'),RDB_EXPECTED_STATE=str(BASE/'a/after-full-state.json'))
        image=load(ROOT/'work/paperless/images.lock.json')['images']['postgres']
        check('pinned PostgreSQL image',run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'],'image.log')==image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-ordered-summary',resource_name],'volume.log');resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',resource_name,'--label','reversedb.experiment=oscar-ordered-summary',
                    '--memory=1024m','--cpus=2','--mount','type=volume,src='+resource_name+',dst=/var/lib/postgresql',
                    '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-analytics-admin',image['resolved'],
                    '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on',
                    '-c','synchronous_commit=on','-c','full_page_writes=on'],'start.log');resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',resource_name,'pg_isready','-h','127.0.0.1','-U','postgres'],f'ready-{i}.log',required=False):break
            time.sleep(.25)
        else:raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-analytics-only'",'role.log')
        sql('CREATE ROLE oscar_packet_reader WITH NOSUPERUSER NOBYPASSRLS NOLOGIN','reader-role.log')
        sql('GRANT oscar_packet_reader TO oscar_user','reader-membership.log')
        sql('CREATE DATABASE oscar_seed OWNER oscar_user','database.log')
        with (BASE/'fixture.sql').open('rb') as stream:
            run(DOCKER+['exec','-i',resource_name,'psql','-U','oscar_user','-d','oscar_seed','-X','-v','ON_ERROR_STOP=1'],'restore.log',stdin=stream)
        host=run(DOCKER+['port',resource_name,'5432/tcp'],'port.log')
        assert host.startswith('127.0.0.1:') and '\n' not in host
        env['DATABASE_PORT']=host.split(':')[1]
        print('Seeding with original four-order replay',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/baseline_probe.py','replay',out/'seed'],'seed.log',env=dict(env,DATABASE_NAME='oscar_seed'))
        check('seed equals complete frozen post-order state',load(out/'seed/after-full-state.json')==load(BASE/'a/after-full-state.json'))
        print('Running fixed ordered read-pair mechanism and negative controls',flush=True)
        run([PYTHON,ROOT/'prototype/oscar/ordered_summary_probe.py','contract',out/'mechanism'],'mechanism.log',env=dict(env,DATABASE_NAME='oscar_seed'))
        result=load(out/'mechanism/result.json')
        check('all fixed mechanism cases complete',result['status']=='passed' and result['cases']==11 and result['arm_runs']==24)
        check('diagnostic differences prevent full admission',result['admission'] is False and len(load(out/'mechanism/diagnostic-differences.json'))==2)
        selection=select_complete_contract(load(SCREEN/'prefix-contract.json'))
        save('selection.json',selection)
        check('same DP still retains original prefix',selection==load(SCREEN/'prefix-selection.json') and selection['selection']['objective']==[3,0,0])
        check('all retained sources unchanged',all(sha(Path(p))==h for p,h in report['inputs'].items()))
        save('comparison.json',dict(mechanism_checks=len(result['checks']),seed_checks=len(load(out/'seed/result.json')['checks']),
            read_pair_calls=dict(original=2,ordered=1),full_tables=97,sequences=94,admitted=False,application_replacement=False,
            workload_budget=dict(hypothetical_pairs=6,original_total=187,hypothetical_total=181,not_executed=True),
            performance_claim=None,remaining_obligations=['original Python handoff, callbacks and guards','full PostgreSQL error diagnostics',
            'independent re-read on miss; prior first-read contract alone is insufficient']))
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
