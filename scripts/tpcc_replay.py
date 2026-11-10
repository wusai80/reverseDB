#!/usr/bin/env python3
"""Restore one frozen fixture twice and compare serialized, clock-controlled transactions."""
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/tpcc'
DOCKER = ['docker', '--context', 'desktop-linux']
TABLES = ['warehouse', 'district', 'customer', 'item', 'stock', 'oorder', 'new_order', 'order_line', 'history']
BASELINE = ROOT / 'evidence/tpcc/20261103-c615d3357989'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(left, right):
    """Strict order and values: no timestamp, parameter or response fields are stripped."""
    for name in ['initial-state.json', 'transactions.json', 'final-state.json', 'invariants.json']:
        if json.loads((left / name).read_text()) != json.loads((right / name).read_text()):
            raise ValueError('Replay mismatch: ' + name)


def main():
    env = dict(os.environ, PATH='/opt/homebrew/opt/openjdk/bin:' + os.environ['PATH'])
    def command(args, **kwargs):
        return subprocess.run(args, env=env, check=True, timeout=180, **kwargs)
    def text(args):
        return command(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    endpoint = text(DOCKER + ['context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}'])
    if not endpoint.startswith('unix://'):
        raise RuntimeError('Local Docker required')
    baseline = json.loads((BASELINE / 'run/run.json').read_text())
    assets = json.loads((BASELINE / 'retained-assets.json').read_text())
    for path, metadata in assets.items():
        if digest(ROOT / path) != metadata['sha256']:
            raise RuntimeError('Fixture/distribution changed: ' + path)
    dump = WORK / 'runs/c615d3357989/initial.dump'
    dist = WORK / 'runs/c615d3357989/distribution/benchbase-postgres'
    # Validate the extracted runtime against the retained, hashed distribution.
    import tarfile
    with tarfile.open(WORK / 'source/target/benchbase-postgres.tgz') as package:
        for member in package.getmembers():
            if member.isfile() and (member.name.endswith('.jar')):
                path = dist.parent / member.name
                if digest(path) != hashlib.sha256(package.extractfile(member).read()).hexdigest():
                    raise RuntimeError('Runtime JAR changed: ' + member.name)
    image = baseline['postgres']
    if text(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}']) != image['id']:
        raise RuntimeError('Unexpected PostgreSQL image')
    out = WORK / 'replays' / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    name = 'reversedb-replay-' + out.name
    schedule = ROOT / 'prototype/tpcc/replay-schedule.json'
    inputs = [Path(__file__), ROOT / 'prototype/tpcc/TpccReplay.java', schedule,
              ROOT / 'prototype/tpcc/invariants.sql']
    report = {'status': 'running', 'name': name, 'source': baseline['source'],
              'fixture_sha256': digest(dump), 'image': image,
              'scope': 'Serial order across four connections; fixed per-transaction seeds and JDBC timestamp bindings',
              'inputs': {str(p.relative_to(ROOT)): digest(p) for p in inputs}, 'commands': []}
    def save(path, data):
        path.write_text(json.dumps(data, indent=2) + '\n')
    def logged(args, filename, stdin=None):
        report['commands'].append(args)
        with (out / filename).open('wb') as stream:
            command(args, stdout=stream, stderr=subprocess.STDOUT, stdin=stdin)
    def sql(database, query):
        return text(DOCKER + ['exec', name, 'psql', '-U', 'tpcc', '-d', database,
                             '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query])
    def state(database):
        # Hash every complete row, including timestamps, then hash sorted row hashes.
        result = {}
        for table in TABLES:
            query = f"""SELECT json_build_object('rows',count(*), 'sha256',
              encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h COLLATE "C"),''),'UTF8')),'hex'))
              FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {table} t) rows"""
            result[table] = json.loads(sql(database, query))
        return result
    created = []
    try:
        cp = str(dist / 'benchbase.jar') + os.pathsep + str(dist / 'lib/*')
        classes = out / 'classes'; classes.mkdir()
        logged(['javac', '--release', '23', '-cp', cp, '-d', str(classes),
                str(ROOT / 'prototype/tpcc/TpccReplay.java')], 'compile.log')
        for resource in ['network', 'volume']:
            logged(DOCKER + [resource, 'create', '--label', 'reversedb.run=' + name, name], resource + '.log')
            created.append(resource)
        logged(DOCKER + ['run', '-d', '--pull=never', '--name', name, '--label', 'reversedb.run=' + name,
                        '--network', name, '--cpus=2', '--memory=1536m', '--shm-size=128m',
                        '-p', '127.0.0.1::5432', '--mount', 'type=volume,src=' + name + ',dst=/var/lib/postgresql',
                        '-e', 'POSTGRES_USER=tpcc', '-e', 'POSTGRES_PASSWORD=synthetic-tpcc-only',
                        image['resolved'], '-c', 'shared_buffers=256MB', '-c', 'timezone=UTC',
                        '-c', 'log_line_prefix=%m session=%c tx=%x app=%a db=%d '], 'start.log')
        created.append('container')
        deadline = time.monotonic() + 60
        while subprocess.run(DOCKER + ['exec', name, 'pg_isready', '-U', 'tpcc'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            if time.monotonic() > deadline:
                raise TimeoutError('Database readiness')
            time.sleep(0.5)
        port = text(DOCKER + ['port', name, '5432/tcp'])
        if not port.startswith('127.0.0.1:'):
            raise RuntimeError('Loopback port required')
        save(out / 'container.json', json.loads(text(DOCKER + ['inspect', name])))
        for database in ['replay_a', 'replay_b']:
            folder = out / database; folder.mkdir()
            sql('tpcc', 'CREATE DATABASE ' + database)
            print('Restoring and replaying ' + database, flush=True)
            with dump.open('rb') as stream:
                logged(DOCKER + ['exec', '-i', name, 'pg_restore', '--exit-on-error', '-U', 'tpcc', '-d', database],
                       database + '/restore.log', stdin=stream)
            save(folder / 'initial-state.json', state(database))
            sql('tpcc', "ALTER SYSTEM SET log_statement='all'")
            sql('tpcc', 'SELECT pg_reload_conf()')
            url = f'jdbc:postgresql://{port}/{database}?sslmode=disable&reWriteBatchedInserts=true'
            logged(['java', '-Duser.timezone=UTC', '-Xmx1024m', '-XX:ActiveProcessorCount=2',
                    '-cp', str(classes) + os.pathsep + cp, 'com.oltpbenchmark.api.TpccReplay',
                    url, 'tpcc', 'synthetic-tpcc-only', str(schedule), str(folder / 'transactions.json')],
                   database + '/replay.log')
            sql('tpcc', "ALTER SYSTEM SET log_statement='none'")
            sql('tpcc', 'SELECT pg_reload_conf()')
            save(folder / 'final-state.json', state(database))
            invariants = json.loads(sql(database, (ROOT / 'prototype/tpcc/invariants.sql').read_text()))
            save(folder / 'invariants.json', invariants)
            if len(invariants) != 5 or any(invariants.values()):
                raise RuntimeError('Consistency check failed')
        compare(out / 'replay_a', out / 'replay_b')
        events = json.loads((out / 'replay_a/transactions.json').read_text())
        expected = json.loads(schedule.read_text())['transactions']
        if [event['input'] for event in events] != expected:
            raise RuntimeError('Executed schedule mismatch')
        report.update(status='passed', transactions=len(events),
                      commits=sum(e['outcome'] == 'commit' for e in events),
                      aborts=sum(e['outcome'] == 'abort' for e in events),
                      trace_events=sum(len(e['trace']) for e in events),
                      comparison='initial rows, SQL/bindings/read values/order/outcomes, final rows and invariants identical')
    except BaseException as error:
        report.update(status='failed', error=repr(error))
    finally:
        errors = []
        if 'container' in created:
            try: logged(DOCKER + ['logs', name], 'postgres.log')
            except Exception as error: errors.append(str(error))
        for kind in reversed(created):
            try:
                args = ['rm', '-f', name] if kind == 'container' else [kind, 'rm', name]
                logged(DOCKER + args, 'remove-' + kind + '.log')
            except Exception as error: errors.append(str(error))
        try:
            report['remaining'] = {kind: text(DOCKER + [kind, 'ls', '-q', '--filter', 'label=reversedb.run=' + name])
                                   for kind in ['container', 'volume', 'network']}
            if any(report['remaining'].values()): errors.append('Resources remain')
        except Exception as error: errors.append(str(error))
        report['cleanup_errors'] = errors
        if errors: report['status'] = 'failed'
        save(out / 'run.json', report)
        print(json.dumps({'status': report['status'], 'evidence': str(out), 'error': report.get('error')}), flush=True)
    if report['status'] != 'passed': raise SystemExit(1)


if __name__ == '__main__':
    main()
