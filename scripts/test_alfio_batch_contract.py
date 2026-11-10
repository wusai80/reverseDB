#!/usr/bin/env python3
"""Characterize the original batch contract; do not install an optimized path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile
from urllib.parse import unquote, urlparse
from alfio_baseline import ROOT, WORK, JDK, DOCKER, JAR, SHA, digest

BASE = ROOT / 'evidence/alfio-roundtrip/20261106-17b16f1a21eb'
DECOMPILE = ROOT / 'evidence/alfio-baseline/20261106-788b831dd41c'


def main():
    out = ROOT / 'evidence/alfio-batch-contract' / ('20261106-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    container_name = 'reversedb-alfio-contract-' + out.name
    report = dict(status='running', commands=[], inputs={}, retained_assets={}, cleanup_errors=[])
    resources = []
    env = dict(os.environ, ALFIO_DB_PASSWORD='synthetic-alfio-only')

    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def run(command, log, stdin=None, check=True):
        command = list(map(str, command))
        report['commands'].append(command)
        result = subprocess.run(command, cwd=ROOT, env=env, stdin=stdin,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
        (out / log).write_bytes(result.stdout)
        if check and result.returncode:
            raise RuntimeError('Failed: ' + str(out / log))
        return result.stdout.decode(errors='replace').strip()

    def sql(query, log, database='alfio'):
        return run(DOCKER + ['exec', container_name, 'psql', '-U', 'postgres', '-d', database,
                            '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], log)

    def retain(folder, name):
        path = folder / name
        assert digest(path) == json.loads((folder / 'manifest.json').read_text())[name], name
        report['retained_assets'][str(path)] = digest(path)
        return path

    try:
        assert digest(JAR) == SHA
        report['retained_assets'][str(JAR)] = SHA
        fixture = retain(BASE, 'fixture.sql')
        restored_class = retain(DECOMPILE, 'recompiled/TicketRepository/alfio/repository/TicketRepository.class')
        probe = ROOT / 'prototype/alfio/AlfioBatchContractProbe.java'
        for path in [Path(__file__).resolve(), ROOT / 'scripts/alfio_baseline.py', probe,
                     ROOT / 'prototype/alfio/ordered_batch_candidate.json', ROOT / 'work/paperless/images.lock.json']:
            frozen = out / 'inputs' / path.relative_to(ROOT)
            frozen.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, frozen)
            report['inputs'][str(path.relative_to(ROOT))] = digest(frozen)
        binary = WORK / 'official-binary'
        original = binary / 'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs = [binary / json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/', 'BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary / member) == __import__('hashlib').sha256(jar.read(member)).hexdigest()
        cp = os.pathsep.join(map(str, [original, *libs]))
        classes = out / 'classes'; classes.mkdir()
        empty = out / 'empty'; empty.mkdir()
        run([JDK / 'javac', '--release', '17', '-proc:none', '-implicit:none', '-sourcepath', empty,
             '-cp', cp, '-d', classes, probe], 'compile.log')
        save('protocol.json', dict(
            scope='original batch-method contract characterization, no optimized implementation and no performance claim',
            offline='original and unchanged decompiled/recompiled TicketRepository default method; test proxy supplies original annotated SQL and capturing Spring template',
            database='original method, pinned driver and Spring binder, complete prior fixture restored; eight serial cases with explicit outer harness rollback',
            observer='temporary AFTER UPDATE trigger logs row order; temporary sequence witnesses nontransactional effects; observation trigger is not production behavior',
            state='all public table rows and sequence states checked after each rollback; synthetic temporary sequence intentionally excluded',
            limits=['not a new end-to-end service test', 'not candidate equivalence', 'synthetic update-count vectors are method-fold tests, not driver observations',
                    'no concurrency/deadlock/cancellation characterization', 'no runtime eligibility dispatcher implemented'],
            stop='one contract suite; report unresolved obligations without expanding the candidate'))
        for arm in ['original-offline', 'recompiled-offline']:
            prefix = [classes]
            if arm.startswith('recompiled'):
                prefix.append(restored_class.parents[2])
            path = os.pathsep.join(map(str, [*prefix, original, *libs]))
            run([JDK / 'java', '-Xmx512m', '-XX:ActiveProcessorCount=2', '-cp', path,
                 'research.alfio.AlfioBatchContractProbe', out / arm], arm + '.log')
            origin = json.loads((out / arm / 'origin.json').read_text())['TicketRepository']
            expected = restored_class.parents[2] if arm.startswith('recompiled') else original
            location = urlparse(origin)
            assert location.scheme == 'file' and not location.netloc
            assert Path(unquote(location.path)).resolve() == expected.resolve(), (origin, expected)
        files = sorted(p.name for p in (out / 'original-offline').glob('*.json') if p.name != 'origin.json')
        assert files and set(files) == {p.name for p in (out / 'recompiled-offline').glob('*.json') if p.name != 'origin.json'}
        assert all(json.loads((out / 'original-offline' / f).read_text()) == json.loads((out / 'recompiled-offline' / f).read_text()) for f in files)
        save('offline-comparison.json', dict(equal=True, files=files, normalization='none; only independently checked origin excluded'))
        print('Original/recompiled offline contracts agree; starting isolated database cases.', flush=True)
        image = json.loads((ROOT / 'work/paperless/images.lock.json').read_text())['images']['postgres']
        report['image'] = image
        assert run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log') == image['id']
        run(DOCKER + ['volume', 'create', '--label', 'reversedb.experiment=alfio-batch-contract', container_name], 'volume.log')
        resources.append('volume')
        run(DOCKER + ['run', '-d', '--pull=never', '--name', container_name, '--label', 'reversedb.experiment=alfio-batch-contract',
            '--memory=1024m', '--cpus=2', '--mount', 'type=volume,src=' + container_name + ',dst=/var/lib/postgresql',
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=synthetic-admin-only', image['resolved'],
            '-c', 'timezone=UTC', '-c', 'shared_buffers=128MB', '-c', 'fsync=on', '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'], 'start.log')
        resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER + ['exec', container_name, 'pg_isready', '-U', 'postgres'], f'ready-{i}.log', check=False):
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'", 'role.log', 'postgres')
        sql('CREATE DATABASE alfio OWNER alfio_user', 'database.log', 'postgres')
        with fixture.open('rb') as stream:
            run(DOCKER + ['exec', '-i', container_name, 'psql', '-U', 'alfio_user', '-d', 'alfio', '-X', '-v', 'ON_ERROR_STOP=1'], 'restore.log', stdin=stream)
        port = run(DOCKER + ['port', container_name, '5432/tcp'], 'port.log')
        assert port.startswith('127.0.0.1:') and '\n' not in port
        run([JDK / 'java', '-Xmx512m', '-XX:ActiveProcessorCount=2', '-cp', str(classes) + os.pathsep + cp,
             'research.alfio.AlfioBatchContractProbe', out / 'database', 'jdbc:postgresql://' + port + '/alfio?sslmode=disable'], 'database-cases.log')
        checks = json.loads((out / 'database/checks.json').read_text())
        cases = json.loads((out / 'database/database-cases.json').read_text())
        assert len(cases) == 8 and all(c['passed'] for c in checks) and all(c['fullStateRestored'] for c in cases)
        for path, sha in report['retained_assets'].items():
            assert digest(Path(path)) == sha
        report.update(status='passed', checks=len(checks), database_cases=len(cases), offline_comparison_files=len(files),
                      candidate_status='not_admitted_contract_characterized_only')
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER + ['logs', container_name], 'postgres.log', check=False)
                    run(DOCKER + ['rm', '-f', '-v', container_name], 'cleanup-container.log')
                else:
                    run(DOCKER + ['volume', 'rm', container_name], 'cleanup-volume.log')
            except Exception as error:
                report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']:
            report['status'] = 'failed'
        save('run.json', report)
        manifest = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
        save('manifest.json', manifest)
        assert all(digest(out / p) == sha for p, sha in manifest.items())
        print(json.dumps(dict(status=report['status'], output=str(out), files=len(manifest))), flush=True)
    if report['status'] != 'passed':
        raise RuntimeError('Contract suite incomplete')


if __name__ == '__main__':
    main()
