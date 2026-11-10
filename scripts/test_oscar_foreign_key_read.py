#!/usr/bin/env python3
"""Fixed whole-FK-read specialization differential; no timing."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evidence/oscar-baseline/20261106-ed9f704d86d6'
PROFILE = ROOT / 'evidence/oscar-module-profile/20261107-a70b2db5616e'
SELECT = ROOT / 'evidence/oscar-select-chain/20261107-6857398837e2'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'
PYTHON = ROOT / 'work/oscar/venv/bin/python'
DOCKER = ['docker', '--context', 'desktop-linux']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p):
    return json.loads(p.read_text())


def main():
    out = ROOT / 'evidence/oscar-foreign-key-read' / ('20261108-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    resource_name = 'reversedb-oscar-fk-' + out.name
    resources = []
    report = dict(status='running', checks=[], commands=[], inputs={}, cleanup_errors=[])
    def save(n, value):
        (out / n).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def check(n, ok):
        assert ok, n
        report['checks'].append(n)
    def run(cmd, log, stdin=None, env=None, required=True):
        cmd = list(map(str, cmd)); report['commands'].append(cmd)
        p = subprocess.run(cmd, cwd=ROOT, stdin=stdin, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        (out / log).write_bytes(p.stdout)
        if required and p.returncode:
            raise RuntimeError(str(out / log))
        return p.stdout.decode(errors='replace').strip()
    def sql(q, log):
        return run(DOCKER + ['exec', resource_name, 'psql', '-U', 'postgres', '-d', 'postgres', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', q], log)
    try:
        save('protocol.json', dict(
            local='15 fixed real-read cases: complete fields, identity/cache, new reads, input boundaries, callbacks, custom filter/lookup/manager, deferred source and converter mutation/failure',
            database='original/candidate 15 local cases plus one identical four-order replay per arm',
            candidate='source-bound FK read construction specialization; four cold original compilations included; no row cache; original model constructor and descriptor semantics',
            compared='exact SQL/params, full output field types, model signals/identity, full 97 tables and 94 sequences',
            stop='fixed differential only; no timing, performance reruns, DP change or application installation'))
        for folder, names in [(BASE, ['fixture.sql', 'fixture-request.json', 'fixture-full-state.json', 'source-manifest.json', 'a/after-full-state.json', 'a/result.json']),
                              (PROFILE, ['profiles/bytecode-witness.json']),
                              (SELECT, ['replay/construction-records.json', 'replay/select-bytecode.json'])]:
            manifest = load(folder / 'manifest.json')
            for f in names:
                p = folder / f
                check('retained ' + str(p.relative_to(ROOT)), sha(p) == manifest[f])
                report['inputs'][str(p)] = sha(p)
        for fixture_name in ['fixture-request.json', 'fixture-full-state.json']:
            shutil.copy2(BASE / fixture_name, out / fixture_name)
        for f, h in load(BASE / 'source-manifest.json').items():
            assert sha(SOURCE / f) == h, f
        for w in load(PROFILE / 'profiles/bytecode-witness.json'):
            assert sha(Path(w['source'])) == w['source_sha256'], w['module']
            p = PROFILE / 'profiles/observed-pyc' / (w['module'] + '.pyc')
            assert sha(p) == w['pyc_sha256'], w['module']
            report['inputs'][str(p)] = sha(p)
        check('frozen application and framework sources unchanged', True)
        first = load(FIRST / 'manifest.json')
        for f in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            check('shared core unchanged ' + f, sha(ROOT / 'scripts' / f) == first['inputs/scripts/' + f])
        for p in [Path(__file__).resolve()] + [ROOT / 'prototype/oscar' / f for f in
                   ['foreign_key_read.py', 'foreign_key_read_probe.py', 'baseline_probe.py']]:
            dest = out / 'inputs' / p.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest); report['inputs'][str(p)] = sha(p)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', TZ='UTC',
                   PYTHONPATH=os.pathsep.join(map(str, [SOURCE / 'src', SOURCE / 'sandbox'])),
                   DJANGO_SETTINGS_MODULE='settings', DATABASE_ENGINE='django.db.backends.postgresql',
                   DATABASE_NAME='offline_no_database', DATABASE_HOST='127.0.0.1', DATABASE_PORT='1',
                   DATABASE_USER='oscar_user', DATABASE_PASSWORD='synthetic-template-only',
                   RDB_SELECT_RECORDS=str(SELECT / 'replay/construction-records.json'))
        image = load(ROOT / 'work/paperless/images.lock.json')['images']['postgres']
        check('pinned PostgreSQL image', run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log') == image['id'])
        run(DOCKER + ['volume', 'create', '--label', 'reversedb.experiment=oscar-foreign-key-read', resource_name], 'volume.log'); resources.append('volume')
        run(DOCKER + ['run', '-d', '--pull=never', '--name', resource_name, '--label', 'reversedb.experiment=oscar-foreign-key-read',
                     '--memory=1024m', '--cpus=2', '--mount', 'type=volume,src=' + resource_name + ',dst=/var/lib/postgresql',
                     '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=synthetic-template-admin', image['resolved'],
                     '-c', 'timezone=UTC', '-c', 'shared_buffers=128MB', '-c', 'fsync=on',
                     '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'], 'start.log'); resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER + ['exec', resource_name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], f'ready-{i}.log', required=False):
                break
            time.sleep(.25)
        else:
            raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-template-only'", 'role.log')
        sql('CREATE DATABASE oscar_fixture OWNER oscar_user', 'database.log')
        with (BASE / 'fixture.sql').open('rb') as stream:
            run(DOCKER + ['exec', '-i', resource_name, 'psql', '-U', 'oscar_user', '-d', 'oscar_fixture', '-X', '-v', 'ON_ERROR_STOP=1'], 'restore.log', stdin=stream)
        host = run(DOCKER + ['port', resource_name, '5432/tcp'], 'port.log')
        assert host.startswith('127.0.0.1:') and '\n' not in host
        env['DATABASE_PORT'] = host.split(':')[1]
        for scope in ['local', 'replay']:
            for arm in ['original', 'candidate']:
                db = 'oscar_' + scope + '_' + arm
                sql('CREATE DATABASE ' + db + ' WITH TEMPLATE oscar_fixture OWNER oscar_user', db + '-create.log')
                print('Running ' + scope + ' ' + arm, flush=True)
                run([PYTHON, ROOT / 'prototype/oscar/foreign_key_read_probe.py', scope, out / (scope + '-' + arm)],
                    scope + '-' + arm + '.log', env=dict(env, DATABASE_NAME=db, RDB_FK_ARM=arm))
        a = load(out / 'local-original/local-result.json'); b = load(out / 'local-candidate/local-result.json')
        check('all 15 real-read outputs/errors/model events/exact SQL match', len(a['cases']) == len(b['cases']) == 15 and a['cases'] == b['cases'])
        for case in a['cases']:
            f = case['case'] + '-full-state.json'
            check(case['case'] + ' all tables/sequences match', load(out / 'local-original' / f) == load(out / 'local-candidate' / f))
        a = load(out / 'replay-original/result.json'); b = load(out / 'replay-candidate/result.json')
        check('four original/candidate/baseline order results and identity tokens equal', a['orders'] == b['orders'] == load(BASE / 'a/result.json')['orders'])
        check('complete final database equals original baseline', load(out / 'replay-original/after-full-state.json') == load(out / 'replay-candidate/after-full-state.json') == load(BASE / 'a/after-full-state.json'))
        for number in ['RDB-ONE', 'RDB-THREE', 'RDB-SIX', 'RDB-DIGITAL']:
            x = load(out / 'replay-original' / (number + '-trace.json')); y = load(out / 'replay-candidate' / (number + '-trace.json'))
            check(number + ' exact physical SQL/parameters unchanged',
                  [(e['sql'], e['params']) for e in x if e['kind'] == 'execute'] == [(e['sql'], e['params']) for e in y if e['kind'] == 'execute'])
            check(number + ' original model events and identity preserved',
                  [e for e in x if e['kind'].startswith('python_')] == [e for e in y if e['kind'].startswith('python_')])
        x = load(out / 'replay-original/specialization.json'); y = load(out / 'replay-candidate/specialization.json')
        check('35 actual FK reads and four cold plans', y['state']['counts']['specialized_reads']==35 and y['state']['counts']['cold_compilations']==4)
        check('whole construction path reduced',all(y['counts'][k]<x['counts'][k] for k in ['get_select','compile','build_filter']))
        check('all model construction and converter discovery retained',all(y['counts'][k]==x['counts'][k] for k in ['model_init','from_db','get_converters']))
        save('comparison.json',dict(original=x,candidate=y,local_cases=15,
            replay_checks_per_arm=len(a['checks']),database_calls_per_arm=sum(o['execute_count'] for o in a['orders']),
            performance_claim=None,default_application_modified=False,optimizer_modified=False,
            limits=['Four model shapes derived from original full-row SQL; no data-ID restriction',
                    'Frozen single-process framework/manager/converter configuration, not general backend admission',
                    'Same 187 calls; removes construction path, not model lifecycle or database safety mechanisms']))
        check('all retained inputs remain unchanged', all(sha(Path(p)) == h for p, h in report['inputs'].items()))
        report.update(status='passed', performance_claim=None, optimizer_modified=False, application_installed=False)
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER + ['logs', resource_name], 'postgres.log', required=False)
                    run(DOCKER + ['rm', '-f', '-v', resource_name], 'cleanup-container.log')
                else:
                    run(DOCKER + ['volume', 'rm', resource_name], 'cleanup-volume.log')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        save('run.json', report)
        save('manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'], output=str(out), checks=len(report['checks']))), flush=True)


if __name__ == '__main__':
    main()
