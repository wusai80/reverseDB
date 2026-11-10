#!/usr/bin/env python3
"""One original fixed replay for SELECT construction attribution; no optimizer."""
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
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'
PYTHON = ROOT / 'work/oscar/venv/bin/python'
DOCKER = ['docker', '--context', 'desktop-linux']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p):
    return json.loads(p.read_text())


def main():
    out = ROOT / 'evidence/oscar-select-chain' / ('20261107-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    name = 'reversedb-oscar-select-' + out.name
    resources = []
    report = dict(status='running', checks=[], commands=[], inputs={}, cleanup_errors=[])

    def save(n, value):
        (out / n).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def check(n, ok):
        assert ok, n
        report['checks'].append(n)

    def run(cmd, log, stdin=None, env=None, required=True):
        cmd = list(map(str, cmd))
        report['commands'].append(cmd)
        p = subprocess.run(cmd, cwd=ROOT, stdin=stdin, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        (out / log).write_bytes(p.stdout)
        if required and p.returncode:
            raise RuntimeError(str(out / log))
        return p.stdout.decode(errors='replace').strip()

    def sql(q, log):
        return run(DOCKER + ['exec', name, 'psql', '-U', 'postgres', '-d', 'postgres', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', q], log)

    try:
        save('protocol.json', dict(orders=4, repetitions=1, arm='original only',
            scope='disjoint SELECT as_sql and outer build_filter CPU scopes; query-clone provenance and result ABI',
            boundary_checks='six generic integer-PK query shapes, eight values each; original compiler, no SQL execution',
            controls='fixed source/data/order/seed, original complete-state oracle, original SQL order/parameters',
            stop='one diagnostic replay, no performance comparison, no optimization or default patch'))
        for folder, names in [(BASE, ['fixture.sql', 'fixture-request.json', 'fixture-full-state.json', 'source-manifest.json', 'a/after-full-state.json', 'a/result.json']),
                              (PROFILE, ['profiles/bytecode-witness.json'])]:
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
        check('all original application/framework sources and retained bytecode unchanged', True)
        first = load(FIRST / 'manifest.json')
        for f in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            check('shared core unchanged ' + f, sha(ROOT / 'scripts' / f) == first['inputs/scripts/' + f])
        for p in [Path(__file__).resolve(), ROOT / 'prototype/oscar/select_chain_probe.py', ROOT / 'prototype/oscar/baseline_probe.py']:
            dest = out / 'inputs' / p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            report['inputs'][str(p)] = sha(p)
        image = load(ROOT / 'work/paperless/images.lock.json')['images']['postgres']
        check('pinned PostgreSQL image', run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log') == image['id'])
        run(DOCKER + ['volume', 'create', '--label', 'reversedb.experiment=oscar-select-chain', name], 'volume.log')
        resources.append('volume')
        run(DOCKER + ['run', '-d', '--pull=never', '--name', name, '--label', 'reversedb.experiment=oscar-select-chain',
                     '--memory=1024m', '--cpus=2', '--mount', 'type=volume,src=' + name + ',dst=/var/lib/postgresql',
                     '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=synthetic-select-admin', image['resolved'],
                     '-c', 'timezone=UTC', '-c', 'shared_buffers=128MB', '-c', 'fsync=on',
                     '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'], 'start.log')
        resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER + ['exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], f'ready-{i}.log', required=False):
                break
            time.sleep(.25)
        else:
            raise RuntimeError('readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'synthetic-select-only'", 'role.log')
        sql('CREATE DATABASE oscar_select OWNER oscar_user', 'database.log')
        with (BASE / 'fixture.sql').open('rb') as stream:
            run(DOCKER + ['exec', '-i', name, 'psql', '-U', 'oscar_user', '-d', 'oscar_select', '-X', '-v', 'ON_ERROR_STOP=1'], 'restore.log', stdin=stream)
        host = run(DOCKER + ['port', name, '5432/tcp'], 'port.log')
        assert host.startswith('127.0.0.1:') and '\n' not in host
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', TZ='UTC',
                   PYTHONPATH=os.pathsep.join(map(str, [SOURCE / 'src', SOURCE / 'sandbox'])),
                   DJANGO_SETTINGS_MODULE='settings', DATABASE_ENGINE='django.db.backends.postgresql',
                   DATABASE_NAME='oscar_select', DATABASE_HOST='127.0.0.1', DATABASE_PORT=host.split(':')[1],
                   DATABASE_USER='oscar_user', DATABASE_PASSWORD='synthetic-select-only', RDB_SELECT_PROFILE=str(PROFILE))
        print('Running one fixed original replay with SELECT construction attribution', flush=True)
        run([PYTHON, ROOT / 'prototype/oscar/select_chain_probe.py', 'replay', out / 'replay'], 'replay.log', env=env)
        analysis = load(out / 'replay/select-analysis.json')
        result = load(out / 'replay/result.json')
        check('all four original order results and identity tokens match', result['orders'] == load(BASE / 'a/result.json')['orders'])
        check('all 97 tables and 94 sequences match original final state', load(out / 'replay/after-full-state.json') == load(BASE / 'a/after-full-state.json'))
        check('SELECT scope and boundary analysis passed', analysis['status'] == 'passed')
        check('all retained inputs remain unchanged', all(sha(Path(p)) == h for p, h in report['inputs'].items()))
        report.update(status='passed', replay_checks=len(result['checks']), analysis_checks=len(analysis['checks']),
                      optimizer_modified=False, application_optimized=False, performance_claim=None)
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER + ['logs', name], 'postgres.log', required=False)
                    run(DOCKER + ['rm', '-f', '-v', name], 'cleanup-container.log')
                else:
                    run(DOCKER + ['volume', 'rm', name], 'cleanup-volume.log')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        save('run.json', report)
        save('manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'], output=str(out), checks=len(report['checks']))), flush=True)


if __name__ == '__main__':
    main()
