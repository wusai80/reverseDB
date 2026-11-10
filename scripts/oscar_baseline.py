#!/usr/bin/env python3
"""Run one bounded upstream-test/sandbox baseline with unchanged shared optimizer."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

from placement_frontend import select_complete_contract

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/oscar'
SOURCE = WORK / 'source-a5ed3c9c'
PYTHON = WORK / 'venv/bin/python'
DOCKER = ['docker', '--context', 'desktop-linux']
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
PRIOR = ROOT / 'evidence/nonjava-selection/20261106-63e43bffe340'


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out = ROOT / 'evidence/oscar-baseline' / ('20261106-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    name = 'reversedb-oscar-' + out.name
    resources = []
    report = dict(status='running', checks=[], cleanup_errors=[], commands=[],
                  performance_claim=None, optimized_runtime=False, commit='a5ed3c9c17d4cf6ee18aa9795b493172416ab68b')
    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def check(name, ok):
        assert ok, name
        report['checks'].append(name)
    env = dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1', TZ='UTC')
    def run(command, log, *, cwd=ROOT, extra=None, required=True):
        command = list(map(str, command))
        report['commands'].append(command)
        p = subprocess.run(command, cwd=cwd, env=dict(env, **(extra or {})),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        (out / log).write_bytes(p.stdout)
        if required and p.returncode:
            raise RuntimeError(str(out / log))
        return p.stdout.decode(errors='replace').strip()
    def sql(query, log):
        return run(DOCKER + ['exec', name, 'psql', '-U', 'postgres', '-d', 'postgres',
                            '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], log)
    try:
        inputs = [Path(__file__).resolve(), ROOT/'prototype/oscar/baseline_probe.py',
                  WORK/'requirements.lock', WORK/'dependencies.in', WORK/'dependencies-install-report.json',
                  WORK/'dependencies-install.log', WORK/'install.log', ROOT/'work/paperless/images.lock.json']
        for n in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py', 'placement_frontend.py']:
            inputs.append(ROOT/'scripts'/n)
        for p in inputs:
            dst = out/'inputs'/p.relative_to(ROOT)
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dst)
        prior = json.loads((PRIOR/'run.json').read_text())
        for path, item in prior['source_inputs'].items():
            check('pinned source ' + path, digest(SOURCE/path) == item['sha256'])
        archive = Path('/private/tmp/reversedb-oscar-a5ed3c9c.tar.gz')
        source_files = {}
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                relative = Path(member.name).relative_to(member.name.split('/')[0])
                sha = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                assert digest(SOURCE/relative) == sha, relative
                source_files[str(relative)] = sha
        save('source-manifest.json', source_files)
        report['archive_sha256'] = digest(archive)
        shutil.copy2(archive, out/'upstream-source.tar.gz')
        check('all archive source files unchanged', len(source_files) > 1000)
        check('same CPython binary as selection', digest(Path(sys.executable)) == prior['interpreter_sha256'])
        for file in ['src/oscar/apps/order/utils.py', 'src/oscar/apps/partner/abstract_models.py']:
            py_compile.compile(str(SOURCE/file), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        for file in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            check('first-project core unchanged ' + file, digest(ROOT/'scripts'/file) == digest(FIRST/'inputs/scripts'/file))
        for case in ['neworder', 'payment']:
            contract = json.loads((FIRST/(case+'-contract.json')).read_text())
            check(case + ' original placement unchanged', select_complete_contract(contract)['selection'] == json.loads((FIRST/(case+'-selection.json')).read_text()))
        run([PYTHON, '-m', 'pip', 'check'], 'pip-check.log')
        freeze = run([PYTHON, '-m', 'pip', 'freeze'], 'requirements-observed.txt')
        check('dependencies exactly match frozen environment', freeze == (WORK/'requirements.lock').read_text().strip())
        image = json.loads((ROOT/'work/paperless/images.lock.json').read_text())['images']['postgres']
        report['image'] = image
        check('pinned local PostgreSQL image', run(DOCKER+['image','inspect',image['resolved'],'--format','{{.Id}}'], 'image.log') == image['id'])
        run(DOCKER+['volume','create','--label','reversedb.experiment=oscar-baseline',name], 'volume.log'); resources.append('volume')
        run(DOCKER+['run','-d','--pull=never','--name',name,'--label','reversedb.experiment=oscar-baseline',
            '--memory=1024m','--cpus=2','--mount','type=volume,src='+name+',dst=/var/lib/postgresql',
            '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=synthetic-oscar-admin-only',image['resolved'],
            '-c','timezone=UTC','-c','shared_buffers=128MB','-c','fsync=on','-c','synchronous_commit=on',
            '-c','full_page_writes=on'], 'start.log'); resources.append('container')
        for i in range(50):
            if 'accepting connections' in run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'], f'ready-{i}.log', required=False):
                break
            time.sleep(.25)
        else:
            raise RuntimeError('PostgreSQL readiness timeout')
        sql("CREATE ROLE oscar_user WITH NOSUPERUSER NOBYPASSRLS CREATEDB LOGIN PASSWORD 'synthetic-oscar-only'", 'role.log')
        for db in ['oscar_tests', 'oscar_fixture']:
            sql('CREATE DATABASE ' + db + ' OWNER oscar_user', db+'-create.log')
        host = run(DOCKER+['port',name,'5432/tcp'], 'port.log')
        assert host.startswith('127.0.0.1:') and '\n' not in host
        env.update(DATABASE_ENGINE='django.db.backends.postgresql', DATABASE_HOST='127.0.0.1',
                   DATABASE_PORT=host.split(':')[1], DATABASE_USER='oscar_user', DATABASE_PASSWORD='synthetic-oscar-only')
        tests_env = dict(PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src', SOURCE])),
                         DJANGO_SETTINGS_MODULE='tests.settings', DATABASE_NAME='oscar_tests')
        print('Running fixed upstream order tests', flush=True)
        run([PYTHON,'-m','pytest','-q','tests/integration/order/test_creator.py',
             'tests/integration/order/test_stockrecord_locking.py','--junitxml='+str(out/'upstream-tests.xml')],
            'upstream-tests.log', cwd=SOURCE, extra=tests_env)
        suites = ET.parse(out/'upstream-tests.xml').getroot().findall('testsuite')
        check('upstream order tests all run and pass', sum(int(s.attrib['tests']) for s in suites) == 24
              and all(int(s.attrib.get(k, 0)) == 0 for s in suites for k in ['errors','failures','skipped']))
        report['upstream_tests'] = 24
        base_env = dict(PYTHONPATH=os.pathsep.join(map(str,[SOURCE/'src', SOURCE/'sandbox'])),
                        DJANGO_SETTINGS_MODULE='settings', DATABASE_NAME='oscar_fixture')
        run([PYTHON,'-m','django','check'], 'sandbox-check.log', extra=base_env)
        run([PYTHON,'-m','django','migrate','--noinput'], 'sandbox-migrate.log', extra=base_env)
        probe = ROOT/'prototype/oscar/baseline_probe.py'
        run([PYTHON,probe,'prepare',out], 'fixture-prepare.log', extra=base_env)
        run(DOCKER+['exec',name,'pg_dump','-U','oscar_user','-d','oscar_fixture','--no-owner','--no-privileges'], 'fixture.sql')
        for arm in ['a', 'b']:
            print('Running original sandbox replay '+arm, flush=True)
            db = 'oscar_' + arm
            sql('CREATE DATABASE '+db+' WITH TEMPLATE oscar_fixture OWNER oscar_user', arm+'-create.log')
            run([PYTHON,probe,'replay',out/arm], arm+'-probe.log', extra=dict(base_env, DATABASE_NAME=db))
        a = json.loads((out/'a/result.json').read_text()); b = json.loads((out/'b/result.json').read_text())
        check('both original replays pass', a['status'] == b['status'] == 'passed')
        check('complete results identical on replay', a == b)
        check('all tables and sequences identical on replay', (out/'a/after-full-state.json').read_bytes() == (out/'b/after-full-state.json').read_bytes())
        for p in (out/'a').glob('*-trace.json'):
            def sql_sequence(path):
                return [(e['kind'], re.sub(r'"s[0-9]+_x[0-9]+"', '"SAVEPOINT_ID"', e.get('sql', '')), e.get('params'), e.get('rowcount'), e.get('exception'))
                        for e in json.loads(path.read_text()) if e['kind'] in ('execute','executemany','commit','rollback')]
            check(p.name+' stable DB-API order and parameters', sql_sequence(p) == sql_sequence(out/'b'/p.name))
        report.update(status='passed', sandbox_checks=len(a['checks']), table_count=a['table_count'], sequence_count=a['sequence_count'], orders=a['orders'])
        save('scope.json', dict(method='existing source-bound effects/ABI contracts and ordered-placement-dp/v1 unchanged',
             result='original baseline and runtime-to-bytecode witness only; no admitted replacement or performance measurement',
             next='verify the allocate UPDATE/refresh boundary including original model initialization, signals, missing rows, triggers and same-instance refresh before any backend claim',
             retained='all signals, analytics, full rows, lock order, default isolation, WAL and transaction guarantees',
             limits=['upstream tests use upstream test configuration; replay uses original sandbox settings',
                     'direct order placement, not browser/HTTP checkout', 'frozen fixture/time/seed only in measurement harness',
                     'raw DB-API traces retained; only process-specific savepoint names normalized for repeatability comparison']))
        for file, sha in source_files.items():
            assert digest(SOURCE/file) == sha, file
        check('upstream source unchanged after execution', True)
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER+['logs',name], 'postgres.log', required=False)
                    run(DOCKER+['rm','-f','-v',name], 'cleanup-container.log')
                else:
                    run(DOCKER+['volume','rm',name], 'cleanup-volume.log')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        save('run.json', report)
        save('manifest.json', {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'], output=str(out), checks=len(report['checks']), upstream_tests=report.get('upstream_tests'), sandbox_checks=report.get('sandbox_checks'))), flush=True)


if __name__ == '__main__':
    main()
