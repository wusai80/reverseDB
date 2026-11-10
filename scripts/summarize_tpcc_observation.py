#!/usr/bin/env python3
"""Correlate command IDs, JDBC calls, PostgreSQL transaction tags and JFR observations."""
import argparse
import collections
import hashlib
import gzip
import json
import re
import zipfile
from pathlib import Path


def summarize(directory):
    directory = Path(directory)
    run = json.loads((directory / 'run.json').read_text())
    if run['status'] != 'passed' or not run.get('observe'):
        raise ValueError('A passed observed execution is required')
    if run['cleanup_errors'] or any(run['remaining'].values()):
        raise ValueError('Cleanup is incomplete')
    server = collections.defaultdict(list)
    for number, line in enumerate((directory / 'postgres.log').read_text().splitlines(), 1):
        match = re.search(r'session=(\S+) .*app=(rdb-t\d+-s\d+) db=(\w+) LOG:  (?:execute [^:]+:|statement:) (.*)', line)
        if match:
            session, tag, database, sql = match.groups()
            server[database, tag].append({'session': session, 'line': number, 'sql_first_line': sql})
    commands, jfr, loaded, input_hashes = [], {}, {}, {}
    seen = set()
    for database in run['cases']:
        folder = directory / database
        def read(name):
            path = folder / name
            raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_suffix(path.suffix + '.gz').read_bytes())
            input_hashes[f'{database}/{name}'] = hashlib.sha256(raw).hexdigest()
            return json.loads(raw)
        responses = read('responses.json')
        timeline = read('runtime.json')
        traces = {t['id']: t['events'] for t in read('jdbc-trace.json')}
        accepted = [r for r in responses if r['status'] != 'rejected']
        if [r['id'] for r in accepted] != [t['id'] for t in timeline]:
            raise ValueError('Missing or reordered runtime command')
        by_id = {r['id']: r for r in accepted}
        for event in timeline:
            key = database, event['application_name']
            if key in seen:
                raise ValueError('Duplicate transaction tag')
            seen.add(key)
            records = server.get(key, [])
            if not records or len({r['session'] for r in records}) != 1:
                raise ValueError('Missing or ambiguous PostgreSQL session')
            boundary = [r['sql_first_line'] for r in records if r['sql_first_line'] in ['COMMIT', 'ROLLBACK']]
            expected = 'COMMIT' if event['status'] == 'committed' else 'ROLLBACK'
            if boundary != [expected] or event['status'] != by_id[event['id']]['status']:
                raise ValueError('Transaction boundary/status mismatch')
            executions = [e for e in traces[event['id']] if e['op'].startswith('execute')]
            if not executions or any(e.get('outcome') != 'returned' or e.get('duration_ns', -1) < 0 for e in executions):
                raise ValueError('Missing JDBC execution/timing evidence')
            if event['duration_ns'] <= 0:
                raise ValueError('Invalid command duration')
            commands.append({**event, 'database': database, 'postgres_session': records[0]['session'],
                             'server_log_lines': [r['line'] for r in records],
                             'jdbc_calls': dict(collections.Counter(e['op'] for e in executions)),
                             'server_statements': len(records), 'boundary': expected})
        events = read('jfr-events.json')['recording']['events']
        event_counts = collections.Counter(e['type'] for e in events)
        if not event_counts['jdk.SocketRead']:
            raise ValueError('Missing JFR socket observations')
        frames = collections.Counter()
        for event in events:
            stack = event['values'].get('stackTrace') or {}
            for frame in stack.get('frames', []):
                method = frame['method']
                owner = method['type']['name']
                if owner.startswith(('reversedb/tpcc/', 'com/oltpbenchmark/benchmarks/tpcc/')):
                    frames[owner + '.' + method['name']] += 1
        jfr[database] = {'events': dict(event_counts), 'application_stack_frame_occurrences': dict(frames),
                         'scope': 'Sampled execution and event stack traces, not a complete method-call count'}
        class_log = (folder / 'class-load.log').read_text()
        loaded[database] = [line for line in class_log.splitlines()
                            if re.search(r' (?:reversedb\.tpcc\.|com\.oltpbenchmark\.benchmarks\.tpcc\.procedures\.)', line)]
        if database.endswith('candidate') and ' com.oltpbenchmark.' in class_log:
            raise ValueError('Candidate loaded reference classes')
    if set(server) != seen:
        raise ValueError('Unmapped transaction tag in server log')
    counts = collections.Counter((c['database'].rsplit('_', 1)[-1], c['type'], c['status']) for c in commands)
    return {'status': 'passed', 'run': run['name'], 'correlated_commands': len(commands),
            'outcomes': [{'engine': e, 'type': t, 'status': s, 'count': n} for (e,t,s),n in sorted(counts.items())],
            'commands': commands, 'jfr': jfr, 'loaded_application_classes': loaded,
            'scope': 'Observed fixed serial scenarios only; tracing/JFR durations are not benchmark measurements',
            'input_sha256': input_hashes}


def bind_artifacts(directory, decompilation):
    """Ensure decompiled bytes match the business classes actually loaded by this run."""
    directory, decompilation = Path(directory), Path(decompilation)
    manifest = json.loads((decompilation / 'manifest.json').read_text())
    classes = json.loads((directory / 'class-sha256.json').read_text())
    run = json.loads((directory / 'run.json').read_text())
    result = []
    def digest(data):
        return hashlib.sha256(data).hexdigest()
    for item in manifest['inputs']:
        role = item['role']
        if role not in {'reference-target', 'candidate-target', 'candidate-app-target'}:
            continue
        snapshot = Path(item['snapshot'])
        if digest(snapshot.read_bytes()) != item['sha256']:
            raise ValueError('Decompilation input digest mismatch')
        if role == 'reference-target':
            jar, member = item['source'].split('!/', 1)
            with zipfile.ZipFile(jar) as archive:
                actual = digest(archive.read(member))
            engine = 'reference'
            runtime_path = jar
        else:
            member = 'reversedb/tpcc/' + snapshot.name
            key = 'standalone-candidate/' + member
            actual = digest((directory / key).read_bytes())
            if actual != classes[key]:
                raise ValueError('Recorded runtime class changed')
            engine = 'candidate'
            runtime_path = next(c[c.index('-d')+1] for c in run['commands']
                                if c[0]=='javac' and '-d' in c and Path(c[c.index('-d')+1]).name=='standalone-candidate')
        if actual != item['sha256']:
            raise ValueError('Decompiled class differs from monitored class')
        class_name = member.removesuffix('.class').replace('/', '.')
        loaded_in = []
        for database in run['cases']:
            if not database.endswith(engine):
                continue
            text = (directory / database / 'class-load.log').read_text()
            if any(' ' + class_name + ' source: ' in line and runtime_path in line for line in text.splitlines()):
                loaded_in.append(database)
        if not loaded_in:
            raise ValueError('Class has no matching runtime load evidence: ' + class_name)
        result.append({'class': class_name, 'engine': engine, 'sha256': actual, 'loaded_in': loaded_in,
                       'decompiler_snapshot': str(snapshot)})
    if not result:
        raise ValueError('No selected decompilation targets')
    return {'status': 'passed', 'matched_classes': len(result), 'classes': result,
            'scope': 'Exact class bytes and load origin; does not establish that every method or branch executed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--decompilation', type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    if args.decompilation:
        binding = bind_artifacts(args.directory, args.decompilation)
        (args.directory / 'artifact-binding.json').write_text(json.dumps(binding, indent=2) + '\n')
        result['artifact_binding'] = {'status': binding['status'], 'matched_classes': binding['matched_classes']}
    (args.directory / 'observation-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'correlated_commands': result['correlated_commands']}))
