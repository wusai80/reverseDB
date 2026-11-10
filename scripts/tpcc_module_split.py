#!/usr/bin/env python3
"""Build and validate one experimental order/stock responsibility boundary."""
import difflib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from rebuild_tpcc import validate_bundle, verify_loaded_classes, digest, JDK
from tpcc_app import compare_app

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'work/tpcc/rebuilds/c3cb2330433f'
SOURCES = ROOT / 'prototype/tpcc/module-split/reversedb/tpcc'
PREFIX = 'reversedb/tpcc/'
REPLACED = {PREFIX + name + '.class' for name in ['NewOrderService', 'NewOrderService$Line', 'NewOrderService$InvalidItem']}
ADDED = PREFIX + 'NewOrderStock.class'
FROZEN = ROOT / 'evidence/tpcc-full/20261103-b7157edda001/run/holdout_candidate'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def execution_events(path):
    return [{'id': row['id'], 'events': [event for event in row['events']
             if event['op'].startswith('execute') or event['op'] in ['commit', 'rollback']]}
            for row in read(path / 'jdbc-trace.json')]


def assess(control, split, commands):
    compare_app(control, split)
    ids = [c['id'] for c in commands]
    selected = set(ids)
    for file in ['responses.json', 'observations.json']:
        expected = [r for r in read(FROZEN / file) if r['id'] in selected]
        require(read(control / file) == expected, 'Control differs from frozen NewOrder evidence: ' + file)
    control_events, split_events = execution_events(control), execution_events(split)
    require(control_events == split_events, 'SQL execution or transaction boundaries changed')
    responses = read(split / 'responses.json')
    require([r['id'] for r in responses] == ids, 'Missing/reordered response')
    counts = {status: sum(r['status'] == status for r in responses) for status in ['committed', 'aborted', 'rejected']}
    require(counts == dict(committed=6, aborted=3, rejected=2), 'Outcome coverage changed')
    observations = {r['id']: r for r in read(split / 'observations.json')}
    boundaries = {r['id']: [e['op'] for e in r['events'] if e['op'] in ['commit', 'rollback']] for r in split_events}
    for row in responses:
        if row['status'] == 'committed': require(boundaries[row['id']] == ['commit'], 'Unexpected commit count')
        elif row['status'] == 'aborted':
            require(boundaries[row['id']] == ['rollback'], 'Unexpected rollback count')
            require(observations[row['id']]['before'] == observations[row['id']]['after'], 'Abort left observed changes')
        else: require(row['id'] not in boundaries, 'Rejected input executed business SQL')
    duplicate = observations['new-05']
    quantities = {when: next(r for r in duplicate[when]['stock'] if r['s_i_id'] == 6) for when in ['before', 'after']}
    return {'status': 'passed', 'cases': len(commands), 'outcomes_per_implementation': counts,
            'business_comparison': 'responses, stepwise observations, initial/final nine-table digests and invariants match',
            'execution_comparison': 'same SQL executions/results and business commit/rollback order; setter/addBatch ordering not required',
            'duplicate_item_6': {when: {k: row[k] for k in ['s_quantity', 's_ytd', 's_order_cnt']} for when, row in quantities.items()},
            'scope': 'Selected NewOrder examples only; no universal capability, independence, concurrency or fault-equivalence claim'}


def main():
    parent = validate_bundle(BASE)
    out = ROOT / 'work/tpcc/module-split' / uuid.uuid4().hex[:12]; out.mkdir(parents=True)
    shutil.copy2(__file__, out / 'workflow.py')
    report = {'status': 'running', 'runs': {}}
    try:
        bundle = out / 'bundle'; shutil.copytree(BASE, bundle)
        shutil.copy2(bundle / 'build.json', bundle / 'parent-build.json')
        build = read(bundle / 'build.json')
        code = bundle / 'sources/candidate' / PREFIX
        for source in sorted(SOURCES.glob('*.java')):
            shutil.copy2(source, code / source.name)
            build['sources'][str((code / source.name).relative_to(bundle))] = digest(source)
        original = (ROOT / 'app/tpcc/src' / PREFIX / 'NewOrderService.java').read_text()
        modified = (code / 'NewOrderService.java').read_text()
        (out / 'split.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='original/NewOrderService.java', tofile='experimental/NewOrderService.java')))
        for member in REPLACED: (bundle / 'candidate-classes' / member).unlink()
        command = [str(JDK / 'javac'), '--release', '23', '-proc:none', '-implicit:none',
                   '-sourcepath', str(bundle / 'empty-sourcepath'), '-cp', str(bundle / 'candidate-classes'),
                   '-d', str(bundle / 'candidate-classes'), *map(str, sorted(code.glob('NewOrder*.java')))]
        with (out / 'compile.log').open('w') as stream:
            subprocess.run(command, check=True, timeout=120, stdout=stream, stderr=subprocess.STDOUT)
        actual = {str(p.relative_to(bundle)) for directory in ['candidate-classes', 'reference-adapter']
                  for p in (bundle / directory).rglob('*.class')} | {'reference.jar'}
        require(actual == set(parent['runtime_files']) | {'candidate-classes/' + ADDED}, 'Unexpected runtime members')
        changed = [p for p,h in parent['runtime_files'].items() if digest(bundle / p) != h]
        require(set(changed) <= {'candidate-classes/' + member for member in REPLACED}, 'Unrelated runtime classes changed')
        require('candidate-classes/' + PREFIX + 'NewOrderService.class' in changed, 'Order implementation was not replaced')
        for member in REPLACED | {ADDED}:
            build['runtime_files']['candidate-classes/' + member] = digest(bundle / 'candidate-classes' / member)
        for row in build['rebuilt_classes']:
            if row['engine'] == 'candidate' and row['member'] in REPLACED:
                row['sha256'] = digest(bundle / 'candidate-classes' / row['member'])
        build['rebuilt_classes'].append(dict(engine='candidate', member=ADDED, sha256=digest(bundle / 'candidate-classes' / ADDED), original_sha256=None))
        build.update(status='passed', scope='HANDWRITTEN EXPERIMENTAL REFACTOR: NewOrder delegates stock work; not unchanged decompiler output',
                     commands=[command], script_sha256=digest(__file__), parent_build_sha256=digest(bundle / 'parent-build.json'))
        save(bundle / 'build.json', build); validate_bundle(bundle)
        commands = [c for c in read(ROOT / 'app/tpcc/scenarios/holdout.json')['commands'] if c['type'] == 'NewOrder']
        save(out / 'commands.json', dict(version=1, commands=commands))
        save(out / 'protocol.json', {'scope': 'One NewOrder responsibility split; no removal of unobserved data or paths',
            'source': 'Manual refactor of existing candidate source; frozen rebuilt application is the control',
            'changed_runtime_files': changed, 'added_runtime_files': ['candidate-classes/' + ADDED],
            'input_sha256': digest(out / 'commands.json'), 'base_manifest_sha256': digest(BASE / 'build.json'),
            'checks': ['business results and observed states', 'full nine-table end state', 'execution order within existing transaction', 'new class actually loaded'],
            'limits': ['single warehouse', 'serial schedule', 'known invalid-item abort occurs before batch writes', 'result projection and transaction owner retained']})
        for label, runtime in [('control', BASE), ('split', bundle)]:
            print('Executing ' + label + ' (NewOrder-only)', flush=True)
            command = [sys.executable, str(ROOT / 'scripts/tpcc_app.py'), 'run', '--engine', 'candidate',
                       '--commands', str(out / 'commands.json'), '--rebuilt', str(runtime)]
            log = out / (label + '.log')
            with log.open('w') as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
            endings = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
            require(bool(endings), 'Missing run result: ' + label)
            run = Path(endings[-1]['evidence']); report['runs'][label] = str(run); save(out / 'run.json', report)
            require(result.returncode == 0 and endings[-1]['status'] == 'passed', 'Execution failed: ' + label)
            record = read(run / 'run.json')
            require(not record['cleanup_errors'] and not any(record['remaining'].values()), 'Resources remain')
            binding = verify_loaded_classes(run, runtime, require_all=False)
            if label == 'split': require(any(r['member'] == ADDED for r in binding['classes']), 'Stock module was not loaded')
            save(out / (label + '-class-binding.json'), binding)
        report['assessment'] = assess(Path(report['runs']['control']) / 'custom_candidate',
                                      Path(report['runs']['split']) / 'custom_candidate', commands)
        validate_bundle(BASE); validate_bundle(bundle)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        save(out / 'run.json', report)
        print(json.dumps({'status': report['status'], 'evidence': str(out), 'error': report.get('error')}), flush=True)


if __name__ == '__main__':
    main()
