#!/usr/bin/env python3
"""Run or compare the five-transaction independent application and pinned reference."""
import argparse
import hashlib
import json
import os
import subprocess
import shutil
import time
import uuid
from pathlib import Path
from rebuild_tpcc import validate_bundle, verify_loaded_classes

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/tpcc'
DOCKER = ['docker', '--context', 'desktop-linux']
TABLES = ['warehouse', 'district', 'customer', 'item', 'stock', 'oorder', 'new_order', 'order_line', 'history']
BASELINE = ROOT / 'evidence/tpcc/20261103-c615d3357989'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_app(left, right):
    """Compare public business behavior and state; candidate SQL shape can differ."""
    for name in ['initial-state.json', 'responses.json', 'final-state.json', 'invariants.json']:
        if json.loads((left / name).read_text()) != json.loads((right / name).read_text()):
            raise ValueError('Application mismatch: ' + name)
    if (left / 'observations.json').exists() or (right / 'observations.json').exists():
        if not (left / 'observations.json').exists() or not (right / 'observations.json').exists():
            raise ValueError('Missing per-command observations')
        if json.loads((left / 'observations.json').read_text()) != json.loads((right / 'observations.json').read_text()):
            raise ValueError('Application mismatch: observations.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify', 'run'])
    parser.add_argument('--commands', type=Path, default=ROOT / 'app/tpcc/scenarios/holdout.json')
    parser.add_argument('--engine', choices=['reference', 'candidate'], default='reference')
    parser.add_argument('--fixture', choices=['baseline', 'delivery_edges'], default='baseline')
    parser.add_argument('--observe', action='store_true', help='Collect command/SQL timing, PostgreSQL transaction tags, JFR and class-loading evidence')
    parser.add_argument('--rebuilt', type=Path, help='Run a validated bundle compiled from unchanged decompiler output')
    args = parser.parse_args()
    rebuilt = args.rebuilt.resolve() if args.rebuilt else None
    build = validate_bundle(rebuilt) if rebuilt else None
    if args.action == 'verify':
        scenarios = ['baseline', 'holdout', 'transaction_edges', 'delivery_edges']
        cases = [(scenario + '_' + engine, ROOT / f'app/tpcc/scenarios/{scenario}.json', engine,
                  'delivery_edges' if scenario == 'delivery_edges' else 'baseline')
                 for scenario in scenarios for engine in ['reference', 'candidate']]
    else:
        cases = [('custom_' + args.engine, args.commands.resolve(), args.engine, args.fixture)]
    env = dict(os.environ, TPCC_PASSWORD='synthetic-tpcc-only', TPCC_OBSERVE='1' if args.observe else '0', PATH='/opt/homebrew/opt/openjdk/bin:' + os.environ['PATH'])
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
    out = WORK / 'app-runs' / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    name = 'reversedb-app-' + out.name
    sources = [rebuilt / 'build.json', ROOT / 'scripts/rebuild_tpcc.py'] if rebuilt else sorted((ROOT / 'app/tpcc/src').rglob('*.java'))
    inputs = [Path(__file__), *sources, ROOT / 'prototype/tpcc/invariants.sql',
              *sorted(set(case[1] for case in cases))]
    if any(case[3] == 'delivery_edges' for case in cases):
        inputs.append(ROOT / 'app/tpcc/scenarios/delivery_edges.sql')
    report = {'status': 'running', 'name': name, 'source': baseline['source'],
              'fixture_sha256': digest(dump), 'image': image,
              'scope': 'Five independent candidate transactions, single warehouse/customer ID/serial schedule; candidate runs without BenchBase JAR',
              'inputs': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): digest(p) for p in inputs},
              'commands': [], 'cases': {}, 'observe': args.observe}
    if rebuilt:
        report['rebuild'] = {'bundle': str(rebuilt), 'manifest_sha256': digest(rebuilt / 'build.json'),
                             'scope': build['scope']}
    for source in inputs:
        destination = out / 'inputs' / (source.relative_to(ROOT) if source.is_relative_to(ROOT) else Path('external') / source.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
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
        cp = str(rebuilt / 'reference.jar' if rebuilt else dist / 'benchbase.jar') + os.pathsep + str(dist / 'lib/*')
        candidate_libs = os.pathsep.join(str(dist / 'lib' / name) for name in ['json-20240303.jar', 'postgresql-42.7.4.jar'])
        if rebuilt:
            classes, standalone = rebuilt / 'reference-adapter', rebuilt / 'candidate-classes'
        else:
            classes = out / 'classes'; classes.mkdir()
            standalone = out / 'standalone-candidate'; standalone.mkdir()
            candidate_sources = sorted((ROOT / 'app/tpcc/src/reversedb/tpcc').glob('*.java'))
            logged(['javac', '--release', '23', '-cp', candidate_libs, '-d', str(standalone),
                    *map(str, candidate_sources)], 'compile-candidate-standalone.log')
            logged(['javac', '--release', '23', '-cp', str(standalone) + os.pathsep + cp, '-d', str(classes),
                    str(ROOT / 'app/tpcc/src/com/oltpbenchmark/api/TpccApp.java')], 'compile.log')
        save(out / 'class-sha256.json', {str(p if rebuilt else p.relative_to(out)): digest(p)
                                       for directory in [standalone, classes] for p in sorted(directory.rglob('*.class'))})
        if args.observe:
            logged(['jfr', 'configure', '--input', '/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home/lib/jfr/profile.jfc',
                    '--output', str(out / 'monitoring.jfc'), 'jdk.SocketRead#threshold=0ms',
                    'jdk.SocketWrite#threshold=0ms'], 'jfr-configure.log')
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
        for database, scenario, engine, fixture in cases:
            folder = out / database; folder.mkdir()
            sql('tpcc', 'CREATE DATABASE ' + database)
            print('Restoring and executing ' + database, flush=True)
            with dump.open('rb') as stream:
                logged(DOCKER + ['exec', '-i', name, 'pg_restore', '--exit-on-error', '-U', 'tpcc', '-d', database],
                       database + '/restore.log', stdin=stream)
            if fixture == 'delivery_edges':
                setup = ROOT / 'app/tpcc/scenarios/delivery_edges.sql'
                (folder / 'fixture-setup.sql').write_text(setup.read_text())
                sql(database, setup.read_text())
            save(folder / 'initial-state.json', state(database))
            save(folder / 'fixture-customers.json', json.loads(sql(database, "SELECT json_agg(x) FROM (SELECT DISTINCT ON(c_credit) c_credit,c_id,c_d_id FROM customer WHERE c_w_id=1 AND c_d_id=1 ORDER BY c_credit,c_id) x")))
            sql('tpcc', "ALTER SYSTEM SET log_statement='all'")
            sql('tpcc', 'SELECT pg_reload_conf()')
            url = f'jdbc:postgresql://{port}/{database}?sslmode=disable&reWriteBatchedInserts=true'
            runtime_cp = str(standalone) + os.pathsep + candidate_libs if engine == 'candidate' else str(classes) + os.pathsep + str(standalone) + os.pathsep + cp
            entry = 'reversedb.tpcc.CandidateApp' if engine == 'candidate' else 'com.oltpbenchmark.api.TpccApp'
            monitor_flags = []
            if args.observe:
                monitor_flags = ['-XX:FlightRecorderOptions=stackdepth=128',
                                 f'-XX:StartFlightRecording=filename={folder / "runtime.jfr"},settings={out / "monitoring.jfc"},dumponexit=true']
            if args.observe or rebuilt:
                monitor_flags.append(f'-Xlog:class+load=info:file={folder / "class-load.log"}:uptime,level,tags')
            logged(['java', '-Duser.timezone=UTC', '-Xmx1024m', '-XX:ActiveProcessorCount=2',
                    *monitor_flags,
                    '-cp', runtime_cp, entry,
                    url, 'tpcc', str(scenario), str(folder), engine],
                   database + '/replay.log')
            if args.observe:
                logged(['jfr', 'summary', str(folder / 'runtime.jfr')], database + '/jfr-summary.txt')
                logged(['jfr', 'print', '--json', '--events',
                        'jdk.ExecutionSample,jdk.SocketRead,jdk.SocketWrite,jdk.GarbageCollection,jdk.JavaMonitorEnter,jdk.ThreadPark',
                        str(folder / 'runtime.jfr')], database + '/jfr-events.json')
            sql('tpcc', "ALTER SYSTEM SET log_statement='none'")
            sql('tpcc', 'SELECT pg_reload_conf()')
            save(folder / 'final-state.json', state(database))
            invariants = json.loads(sql(database, (ROOT / 'prototype/tpcc/invariants.sql').read_text()))
            save(folder / 'invariants.json', invariants)
            if len(invariants) != 5 or any(invariants.values()):
                raise RuntimeError('Consistency check failed')
            responses = json.loads((folder / 'responses.json').read_text())
            command_list = json.loads(scenario.read_text())['commands']
            if [r['id'] for r in responses] != [c['id'] for c in command_list]:
                raise RuntimeError('Missing or reordered command response')
            report['cases'][database] = {status: sum(r['status'] == status for r in responses)
                                        for status in ['committed', 'aborted', 'rejected']}
        if args.action == 'verify':
            for scenario in scenarios:
                compare_app(out / (scenario + '_reference'), out / (scenario + '_candidate'))
            old = ROOT / 'evidence/tpcc-replay/20261103-7e3eaafec61d/replay_a'
            for filename in ['initial-state.json', 'final-state.json']:
                if json.loads((old / filename).read_text()) != json.loads((out / 'baseline_reference' / filename).read_text()):
                    raise RuntimeError('Explicit business adapter differs from original seeded baseline: ' + filename)
            if report['cases']['baseline_reference'] != dict(committed=10, aborted=1, rejected=0):
                raise RuntimeError('Baseline outcome coverage changed')
            if report['cases']['holdout_reference'] != dict(committed=10, aborted=3, rejected=4):
                raise RuntimeError('Holdout outcome coverage changed')
            holdout = json.loads((out / 'holdout_reference/responses.json').read_text())
            stock_four = [next(s['s_quantity'] for s in r['state']['stock'] if s['s_i_id'] == 4)
                          for r in holdout[:2]]
            if stock_four != [16, 97]:
                raise RuntimeError('Stock wrap branch was not exercised as expected')
            if report['cases']['transaction_edges_reference'] != dict(committed=23, aborted=0, rejected=4):
                raise RuntimeError('Transaction edge coverage changed')
            if report['cases']['delivery_edges_reference'] != dict(committed=7, aborted=0, rejected=0):
                raise RuntimeError('Delivery edge coverage changed')
            observations = json.loads((out / 'transaction_edges_reference/observations.json').read_text())
            payments = [o for o in observations if 'history' in o['after']]
            if {o['after']['customer'][0]['c_credit'] for o in payments} != {'GC', 'BC'}:
                raise RuntimeError('Both customer credit branches must execute')
            if len(payments[11]['after']['customer'][0]['c_data']) != 500:
                raise RuntimeError('Bad-credit data truncation not covered')
            if not any(o['after']['district'][0]['d_id'] != o['after']['customer'][0]['c_d_id'] for o in payments):
                raise RuntimeError('Cross-district payment not covered')
            delivery = json.loads((out / 'delivery_edges_reference/responses.json').read_text())
            if [len(delivery[i]['result']['delivered']) for i in [0, 1, 2, 4]] != [1, 1, 0, 1]:
                raise RuntimeError('Partial/empty/new-order delivery branch not covered')
            if delivery[5]['result']['order']['o_carrier_id'] != 7:
                raise RuntimeError('Delivered order status does not reflect carrier')
            report['verification'] = 'All five independent transactions: responses, stepwise write observations, nine-table hashes and invariants equal; read-only queries enforced; credit/truncation/cross-district/empty-delivery branches covered'
            if args.observe or rebuilt:
                unmonitored = ROOT / 'evidence/tpcc-full/20261103-b7157edda001/run'
                for database, _, _, _ in cases:
                    compare_app(unmonitored / database, out / database)
                report['frozen_behavior_check' if rebuilt else 'monitoring_behavior_check'] = 'All eight executions match the frozen original business responses and states'
        if rebuilt:
            save(out / 'run.json', report)
            binding = verify_loaded_classes(out, rebuilt, require_all=args.action == 'verify')
            save(out / 'rebuild-class-binding.json', binding)
            report['rebuild']['class_binding'] = binding['status']
            report['rebuild']['loaded_classes'] = binding['class_count']
        report['status'] = 'passed'
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
