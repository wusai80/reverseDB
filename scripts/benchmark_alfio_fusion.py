#!/usr/bin/env python3
"""Frozen alf.io fusion: two warmups and six alternating measured pairs, no tuning."""
import difflib
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import uuid
import zipfile
from urllib.parse import unquote, urlparse
from alfio_baseline import ROOT, WORK, JDK, DOCKER, JAR, SHA, digest
from test_alfio_roundtrip import compare_folders

FUSION = ROOT / 'evidence/alfio-ordered-fusion/20261106-803c10c7d846'
REPLAY = ROOT / 'evidence/alfio-roundtrip/20261106-17b16f1a21eb'
OPERATIONS = ['01-reserve-three', '02-query-first', '03-insufficient-stock', '04-reserve-two', '05-cancel-first',
              '06-process-first', '07-query-second', '08-cancel-second', '09-process-second', '10-reserve-one', '11-cancel-third', '12-process-third']


def timed_source(source):
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    replace('public final class AlfioRoundtripProbe', 'public final class AlfioPerformanceProbe')
    replace('  static final ObjectMapper JSON=new ObjectMapper();',
            '  static final ClockProvider SHARED_CLOCK=ClockProvider.init(Clock.fixed(NOW,ZoneOffset.UTC));\n  static final ObjectMapper JSON=new ObjectMapper();')
    replace('definition.setInstanceSupplier(() -> ClockProvider.init(Clock.fixed(NOW,ZoneOffset.UTC)));',
            'definition.setInstanceSupplier(() -> SHARED_CLOCK);')
    calls = [
        ('String a=reserve(manager,event,category,3)', 'String a=AlfioTiming.call("01-reserve-three",()->reserve(manager,event,category,3))'),
        ('var summary=manager.orderSummaryForReservationId(a,event)', 'var summary=AlfioTiming.call("02-query-first",()->manager.orderSummaryForReservationId(a,event))'),
        ('try { reserve(manager,event,category,21);', 'try { AlfioTiming.call("03-insufficient-stock",()->reserve(manager,event,category,21));'),
        ('String b=reserve(manager,event,category,2)', 'String b=AlfioTiming.call("04-reserve-two",()->reserve(manager,event,category,2))'),
        ('manager.cancelPendingReservation(a,false,username);', 'AlfioTiming.run("05-cancel-first",()->manager.cancelPendingReservation(a,false,username));'),
        ('processor.handleWaitingTickets(); inventory(observer,18,2,', 'AlfioTiming.run("06-process-first",()->processor.handleWaitingTickets()); inventory(observer,18,2,'),
        ('var secondSummary=manager.orderSummaryForReservationId(b,event)', 'var secondSummary=AlfioTiming.call("07-query-second",()->manager.orderSummaryForReservationId(b,event))'),
        ('manager.cancelPendingReservation(b,false,username);', 'AlfioTiming.run("08-cancel-second",()->manager.cancelPendingReservation(b,false,username));'),
        ('processor.handleWaitingTickets(); inventory(observer,20,0,"07-', 'AlfioTiming.run("09-process-second",()->processor.handleWaitingTickets()); inventory(observer,20,0,"07-'),
        ('String c=reserve(manager,event,category,1)', 'String c=AlfioTiming.call("10-reserve-one",()->reserve(manager,event,category,1))'),
        ('manager.cancelPendingReservation(c,false,username);', 'AlfioTiming.run("11-cancel-third",()->manager.cancelPendingReservation(c,false,username));'),
        ('processor.handleWaitingTickets(); inventory(observer,20,0,"09-', 'AlfioTiming.run("12-process-third",()->processor.handleWaitingTickets()); inventory(observer,20,0,"09-')]
    for old, new in calls: replace(old, new)
    return source


def main(retained_baseline=False):
    experiment = 'alfio-retained-performance' if retained_baseline else 'alfio-fusion-performance'
    second_arm = 'retained' if retained_baseline else 'candidate'
    def is_candidate(arm): return not retained_baseline and arm == 'candidate'
    out = ROOT / 'evidence' / experiment / ('20261106-' + uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    container_name = 'reversedb-alfio-perf-' + out.name
    report = dict(status='running', mode='original-original' if retained_baseline else 'historical-manual-fusion',
                  inputs={}, retained_assets={}, commands=[], trials=[], cleanup_errors=[])
    resources = []; workers = {}; logs = []
    env = dict(os.environ, ALFIO_DB_PASSWORD='synthetic-alfio-only')

    def save(file, data): (out / file).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    def run(command, log, stdin=None, check=True):
        command = list(map(str, command)); report['commands'].append(command)
        p = subprocess.run(command, cwd=ROOT, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
        (out / log).write_bytes(p.stdout)
        if check and p.returncode: raise RuntimeError('Failed: ' + str(out / log))
        return p.stdout.decode(errors='replace').strip()

    def retain(base, name):
        p = base / name; assert digest(p) == json.loads((base / 'manifest.json').read_text())[name], name
        report['retained_assets'][str(p)] = digest(p); return p

    def sql(query, log, database='alfio'):
        return run(DOCKER + ['exec', container_name, 'psql', '-U', 'postgres', '-d', database, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], log)

    try:
        assert digest(JAR) == SHA; report['retained_assets'][str(JAR)] = SHA
        fixture = retain(REPLAY, 'fixture.sql'); entropy = retain(REPLAY, 'entropy-agent.jar')
        source = retain(FUSION, 'generated/AlfioRoundtripProbe.java')
        function = None if retained_baseline else retain(FUSION, 'inputs/prototype/alfio/ordered_batch.sql')
        helper = retain(FUSION, 'probe-classes/research/alfio/AlfioOrderedBatch.class')
        candidate_class = None if retained_baseline else retain(FUSION, 'candidate-classes/alfio/repository/TicketRepository.class')
        if retained_baseline:
            admission = ROOT / 'evidence/grouped-lookup-admission/20261106-abb2849f09ce'
            contract = json.loads(retain(admission, 'contract.json').read_text())
            retain(admission, 'selection.json')
            assert all(n['database'] is None for n in contract['nodes'])
            assert {n['id'] for n in contract['nodes'] if n.get('host_retention', {}).get('required')} == {'read_items', 'lookup_definitions'}
            # Freeze the actual executed selector input, not an assumed optimized runtime.
            from placement_frontend import select_complete_contract
            assert select_complete_contract(contract) == json.loads((admission / 'selection.json').read_text())
            for name, sha in json.loads(retain(admission, 'run.json').read_text())['inputs'].items():
                assert digest(ROOT / name) == sha, name
                report['retained_assets'][str(ROOT / name)] = sha
        for p in sorted((FUSION / 'original').glob('*.json')): retain(FUSION, str(p.relative_to(FUSION)))
        for name in ['scripts/benchmark_alfio_fusion.py', 'scripts/alfio_baseline.py', 'scripts/test_alfio_roundtrip.py',
                     'prototype/alfio/AlfioTiming.java', 'prototype/alfio/AlfioPerformanceWorker.java', 'work/paperless/images.lock.json']:
            src = ROOT / name; dst = out / 'inputs' / name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst); report['inputs'][name] = digest(dst)
        binary = WORK / 'official-binary'; original = binary / 'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs = [binary / json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/', 'BOOT-INF/lib/')) and not member.endswith('/'):
                    assert digest(binary / member) == __import__('hashlib').sha256(jar.read(member)).hexdigest()
        classes = out / 'classes'; classes.mkdir(); empty = out / 'empty'; empty.mkdir(); generated = out / 'generated'; generated.mkdir()
        probe = generated / 'AlfioPerformanceProbe.java'; probe.write_text(timed_source(source.read_text()))
        (generated / 'timing.diff').write_text(''.join(difflib.unified_diff(source.read_text().splitlines(True), probe.read_text().splitlines(True), fromfile='frozen-replay', tofile='timed-replay')))
        common = [FUSION / 'probe-classes', original, *libs, entropy]
        run([JDK / 'javac', '--release', '17', '-proc:none', '-implicit:none', '-sourcepath', empty, '-cp', os.pathsep.join(map(str, common)), '-d', classes,
             probe, ROOT / 'prototype/alfio/AlfioTiming.java', ROOT / 'prototype/alfio/AlfioPerformanceWorker.java'], 'compile.log')
        schedule = [dict(pair=i + 1, phase='warmup' if i < 2 else 'measured', order=['original', second_arm] if i % 2 == 0 else [second_arm, 'original']) for i in range(8)]
        save('protocol.json', dict(mode=report['mode'], schedule=schedule, operations=OPERATIONS,
            fixture='same full archived 20-ticket, 63-table fixture every trial; ' + ('no experimental functions installed' if retained_baseline else 'both arms install identical function'),
            implementation=('both arms use identical official application classes; no admitted runtime transformation; historical helper only records empty dispatch' if retained_baseline else 'exact frozen candidate and helper class files; no candidate source recompilation or performance tuning'),
            jvm='one persistent JVM per arm, same flags; fresh original Spring context and pools each trial; reuse one injected fixed ClockProvider (upstream init is one-shot); test observations reset outside timing',
            timing='System.nanoTime around each full service/processor call, through synchronous commit/rollback and returned Java result; include request construction for reservation calls',
            excluded='JVM/context startup and shutdown, fixture restore, connection-pool startup, ANALYZE/CHECKPOINT, all full-state observation and JSON serialization to evidence files',
            metrics=['12-operation accumulated elapsed', 'three successful reservation calls accumulated elapsed', 'other nine operations accumulated elapsed'],
            correctness='53 complete JSON files compared to frozen original each trial, entropy metadata/site counts and candidate dispatch independently checked',
            stop='two warmup pairs then six measured pairs regardless of outcome; no additional samples',
            limitations=['short serial workload, not throughput', 'not steady-state pools or server plan caches', 'two warmups do not guarantee JIT convergence', 'source-control and candidate telemetry overhead retained', 'small local Docker sample without statistical significance claim'] +
                (['A/A repeatability only, no acceleration claim', 'fixture contains no additional services: cannot estimate G>0 grouped-lookup performance'] if retained_baseline else [])))
        image = json.loads((ROOT / 'work/paperless/images.lock.json').read_text())['images']['postgres']; report['image'] = image
        assert run(DOCKER + ['image', 'inspect', image['resolved'], '--format', '{{.Id}}'], 'image.log') == image['id']
        run(DOCKER + ['volume', 'create', '--label', 'reversedb.experiment=' + experiment, container_name], 'volume.log'); resources.append('volume')
        run(DOCKER + ['run', '-d', '--pull=never', '--name', container_name, '--label', 'reversedb.experiment=' + experiment, '--memory=1536m', '--cpus=2',
            '--mount', 'type=volume,src=' + container_name + ',dst=/var/lib/postgresql', '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=synthetic-admin-only', image['resolved'],
            '-c', 'timezone=UTC', '-c', 'shared_buffers=256MB', '-c', 'fsync=on', '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'], 'start.log'); resources.append('container')
        for i in range(40):
            if 'accepting connections' in run(DOCKER + ['exec', container_name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], f'ready-{i}.log', check=False): break
            time.sleep(.5)
        else: raise RuntimeError('Database readiness timeout')
        sql("CREATE ROLE alfio_user WITH NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'synthetic-alfio-only'", 'role.log', 'postgres')
        port = run(DOCKER + ['port', container_name, '5432/tcp'], 'port.log'); assert port.startswith('127.0.0.1:') and '\n' not in port
        url = 'jdbc:postgresql://' + port + '/alfio?sslmode=disable'
        settings = json.loads(sql("select json_object_agg(name,setting) from pg_settings where lower(name) in ('fsync','synchronous_commit','full_page_writes','wal_level','timezone','server_version','autovacuum')", 'settings.log', 'postgres'))
        assert all(settings[k] == 'on' for k in ['fsync', 'synchronous_commit', 'full_page_writes', 'autovacuum']); save('settings.json', settings)
        for arm in ['original', second_arm]:
            paths = [classes, *([FUSION / 'candidate-classes'] if is_candidate(arm) else []), *common]
            command = list(map(str, [JDK / 'java', '-Xmx1024m', '-XX:ActiveProcessorCount=2', '-Duser.timezone=UTC', '-Dreversedb.alfio.orderedBatch=' + str(is_candidate(arm)).lower(),
                '-javaagent:' + str(entropy), '-cp', os.pathsep.join(map(str, paths)), 'research.alfio.AlfioPerformanceWorker', out / (arm + '-origin.json')]))
            report['commands'].append(command)
            log = (out / (arm + '-worker.log')).open('wb'); logs.append(log)
            workers[arm] = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
        expected_transformations = json.loads((FUSION / 'original/transformations.json').read_text())
        for pair in schedule:
            for arm in pair['order']:
                label = f"{pair['pair']:02d}-{pair['phase']}-{arm}"
                sql('DROP DATABASE IF EXISTS alfio', label + '-drop.log', 'postgres'); sql('CREATE DATABASE alfio OWNER alfio_user', label + '-create.log', 'postgres')
                for path, suffix in [(fixture, 'restore')] + ([(function, 'function')] if function is not None else []):
                    with path.open('rb') as stream:
                        run(DOCKER + ['exec', '-i', container_name, 'psql', '-U', 'alfio_user', '-d', 'alfio', '-X', '-v', 'ON_ERROR_STOP=1'], label + '-' + suffix + '.log', stdin=stream)
                sql('ANALYZE', label + '-analyze.log'); sql('CHECKPOINT', label + '-checkpoint.log')
                worker = workers[arm]; worker.stdin.write(json.dumps(dict(url=url, output=str(out / label))) + '\n'); worker.stdin.flush()
                deadline = time.monotonic() + 240; done = out / (label + '-done.json')
                while not done.exists():
                    if worker.poll() is not None: raise RuntimeError(arm + ' worker exited; see log')
                    if time.monotonic() > deadline: raise TimeoutError(label)
                    time.sleep(.2)
                assert json.loads(done.read_text()) == dict(status='passed', operations=12), done
                comparison = compare_folders(FUSION / 'original', out / label); save(label + '-comparison.json', comparison)
                assert comparison['equal'] and comparison['compared_files'] == 53, (label, comparison)
                transformed = json.loads((out / label / 'transformations.json').read_text())
                assert transformed.keys() == expected_transformations.keys(), label
                for key, value in transformed.items():
                    target = dict(expected_transformations[key])
                    if is_candidate(arm) and key == 'alfio/repository/TicketRepository': target['input_sha256'] = digest(candidate_class)
                    assert value == target, (label, key)
                dispatch = json.loads((out / (label + '-dispatch.json')).read_text())
                if is_candidate(arm):
                    assert dispatch == [dict(path=p, rows=n) for n in [3,2,1] for p in ['candidate-attempt','candidate-return']], dispatch
                else: assert dispatch == []
                timings = json.loads((out / (label + '-timing.json')).read_text())
                assert [t['operation'] for t in timings] == OPERATIONS and all(t['nanoseconds'] > 0 for t in timings)
                assert [t['operation'] for t in timings if not t['returned']] == ['03-insufficient-stock']
                total = sum(t['nanoseconds'] for t in timings) / 1e6
                reservations = sum(t['nanoseconds'] for t in timings if t['operation'] in ['01-reserve-three','04-reserve-two','10-reserve-one']) / 1e6
                trial = dict(label=label, pair=pair['pair'], phase=pair['phase'], arm=arm, total_ms=total, reservations_ms=reservations, other_ms=total-reservations,
                             compared_files=53, operations=12)
                report['trials'].append(trial); save('progress.json', report['trials'])
                print(json.dumps(trial), flush=True)
        for arm in workers:
            origin = json.loads((out / (arm + '-origin.json')).read_text())
            expected = FUSION / 'candidate-classes' if is_candidate(arm) else original
            assert Path(unquote(urlparse(origin['repository']).path)).resolve() == expected.resolve()
            assert Path(unquote(urlparse(origin['helper']).path)).resolve() == (FUSION / 'probe-classes').resolve()
        summary = dict(metrics={}, pairs=[], warmup_pairs=2, measured_pairs=6, all_trials=16, total_operations=192, measured_operations=144,
                       mode=report['mode'],
                       interpretation=('A/A original runtime repeatability; differences are measurement variation, not optimization gains; ' if retained_baseline else '') + 'bounded short serial workload with new context/pools per trial; no statistical significance or throughput claim')
        measured = [t for t in report['trials'] if t['phase'] == 'measured']
        for metric in ['total_ms', 'reservations_ms', 'other_ms']:
            a = [t[metric] for t in measured if t['arm'] == 'original']; b = [t[metric] for t in measured if t['arm'] == second_arm]
            x, y = statistics.median(a), statistics.median(b)
            ratios = [next(t[metric] for t in measured if t['pair'] == pair and t['arm'] == 'original') / next(t[metric] for t in measured if t['pair'] == pair and t['arm'] == second_arm) for pair in range(3,9)]
            summary['metrics'][metric] = dict(original_median=x, ratio_of_medians=x/y, original_range=[min(a),max(a)],
                paired_ratios=ratios, paired_ratio_median=statistics.median(ratios),
                **{second_arm+'_median': y, second_arm+'_change_pct': (y/x-1)*100,
                   second_arm+'_range': [min(b),max(b)], second_arm+'_faster_pairs': sum(r>1 for r in ratios)})
        for pair in range(3,9):
            summary['pairs'].append(dict(pair=pair-2, order=schedule[pair-1]['order'], original=next(t for t in measured if t['pair']==pair and t['arm']=='original'),
                **{second_arm: next(t for t in measured if t['pair']==pair and t['arm']==second_arm)}))
        save('summary.json', summary)
        for path, sha in report['retained_assets'].items(): assert digest(Path(path)) == sha
        report.update(status='passed', measured_pairs=6, warmup_pairs=2, correctness='all sixteen trials match all 53 frozen original files')
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        for arm, worker in workers.items():
            try:
                if worker.poll() is None:
                    worker.stdin.write('stop\n'); worker.stdin.flush()
                    try: worker.wait(timeout=30)
                    except subprocess.TimeoutExpired: worker.terminate(); worker.wait(timeout=10)
                if worker.returncode != 0: report['cleanup_errors'].append(arm + ' worker exit ' + str(worker.returncode))
            except Exception as error:
                if worker.poll() is None: worker.kill(); worker.wait()
                report['cleanup_errors'].append(repr(error))
        for log in logs: log.close()
        for resource in reversed(resources):
            try:
                if resource == 'container':
                    run(DOCKER + ['logs', container_name], 'postgres.log', check=False); run(DOCKER + ['rm', '-f', '-v', container_name], 'cleanup-container.log')
                else: run(DOCKER + ['volume', 'rm', container_name], 'cleanup-volume.log')
            except Exception as error: report['cleanup_errors'].append(repr(error))
        if report['cleanup_errors']: report['status'] = 'failed'
        save('run.json', report)
        manifest = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()}; save('manifest.json', manifest)
        assert all(digest(out / p) == sha for p, sha in manifest.items())
        print(json.dumps(dict(status=report['status'], output=str(out), files=len(manifest))), flush=True)
    if report['status'] != 'passed': raise RuntimeError('Benchmark incomplete')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retained-baseline', action='store_true', help='A/A: both arms retain official runtime; never install or execute experimental fusion')
    main(parser.parse_args().retained_baseline)
