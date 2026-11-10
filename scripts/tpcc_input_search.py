#!/usr/bin/env python3
"""Bounded deterministic input generation and generic state differential search."""
import argparse
import json
import random
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from rebuild_tpcc import digest, validate_bundle, verify_loaded_classes

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / 'work/tpcc/rebuilds/c3cb2330433f'
MUTANT = ROOT / 'work/tpcc/branch-probes/44307411537f/mutant'
MEMBER = 'reversedb/tpcc/NewOrderService.class'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def generate(seed, budget):
    """Only the selected command schema/domain is known; no target rule or state values."""
    require(1 <= budget <= 20, 'Command budget must be 1..20')
    rng = random.Random(seed)
    return {'version': 1, 'commands': [
        dict(id=f'search-{i + 1:02d}', type='NewOrder', terminal=1, warehouse=1,
             district=1, customer=1, clock_ms=1793750400000 + i * 1000,
             lines=[dict(item=item, quantity=rng.randint(1, 10) if item == 4 else 1)
                    for item in [1, 2, 3, 4, 5]])
        for i in range(budget)]}


def differences(left, right, path=''):
    """JSON-pointer differences; independent of table, column and mutation semantics."""
    if type(left) is not type(right):
        return [dict(path=path, control=left, mutant=right)]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            location = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in left or key not in right:
                result.append(dict(path=location, control=left.get(key), mutant=right.get(key), missing_in='control' if key not in left else 'mutant'))
            else:
                result.extend(differences(left[key], right[key], location))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [dict(path=path, control=left, mutant=right)]
        return [row for i, (a, b) in enumerate(zip(left, right)) for row in differences(a, b, path + '/' + str(i))]
    return [] if left == right else [dict(path=path, control=left, mutant=right)]


def search_trace(commands, responses, observations):
    ids = [c['id'] for c in commands]
    require(len(set(ids)) == len(ids), 'Duplicate input IDs')
    for side in range(2):
        require([r['id'] for r in responses[side]] == ids, 'Missing/reordered responses')
        require([r['id'] for r in observations[side]] == ids, 'Missing/reordered observations')
        require(all(r['status'] == 'committed' for r in responses[side]), 'Unexpected execution failure')
    for index, command in enumerate(commands):
        a, b = observations[0][index], observations[1][index]
        require(a['before'] == b['before'], 'Pre-state differs before first divergence')
        public = differences(responses[0][index]['result'], responses[1][index]['result'], '/result')
        state = differences(a['after'], b['after'], '/after')
        if public or state:
            return {'outcome': 'found', 'first_step': index + 1, 'command_id': command['id'],
                    'public_result_differences': public, 'state_differences': state,
                    'equal_prefix_steps': index, 'compared_steps': index + 1,
                    'witness': commands[:index + 1]}
    return {'outcome': 'not_found', 'compared_steps': len(commands), 'equal_prefix_steps': len(commands),
            'meaning': 'No distinction within this generated sequence and observation scope; not equivalence'}


def assess(commands, control, mutant):
    require(read(control / 'initial-state.json') == read(mutant / 'initial-state.json'), 'Different database snapshots')
    result = search_trace(commands, [read(p / 'responses.json') for p in [control, mutant]],
                          [read(p / 'observations.json') for p in [control, mutant]])
    result['final_table_differences'] = differences(read(control / 'final-state.json'), read(mutant / 'final-state.json'))
    result['invariant_differences'] = differences(read(control / 'invariants.json'), read(mutant / 'invariants.json'))
    if result['outcome'] == 'not_found':
        require(not result['final_table_differences'] and not result['invariant_differences'], 'Difference outside step observer; expand observation scope')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261103)
    parser.add_argument('--budget', type=int, default=6)
    args = parser.parse_args()
    commands = generate(args.seed, args.budget)
    original, mutated = validate_bundle(CONTROL), validate_bundle(MUTANT)
    changed = [p for p, h in original['runtime_files'].items() if mutated['runtime_files'].get(p) != h]
    require(changed == ['candidate-classes/' + MEMBER], 'Expected one fixed target mutation')
    out = ROOT / 'work/tpcc/input-search' / uuid.uuid4().hex[:12]; out.mkdir(parents=True)
    shutil.copy2(__file__, out / 'workflow.py')
    save(out / 'commands.json', commands)
    save(out / 'protocol.json', {
        'seed': args.seed, 'budget': args.budget, 'generator': 'Python random.Random(seed), uniform integer quantity 1..10',
        'scope': 'One manually selected NewOrder family; vary item 4 quantity; other items and fields fixed',
        'search': 'One bounded stateful sequence; batch execution then first-divergence scan',
        'oracle': 'Generic result and observed post-state JSON differences; equal initial state required',
        'no_target_rule_model': True, 'candidate_sequences': 1,
        'control': str(CONTROL), 'mutant': str(MUTANT), 'changed_runtime_files': changed,
        'control_manifest_sha256': digest(CONTROL / 'build.json'), 'mutant_manifest_sha256': digest(MUTANT / 'build.json'),
        'commands_sha256': digest(out / 'commands.json'), 'script_sha256': digest(__file__), 'python': sys.version})
    report = {'status': 'running', 'runs': {}, 'actual_business_commands': 0}

    def execute(label, bundle, input_file):
        print('Executing ' + label, flush=True)
        command = [sys.executable, str(ROOT / 'scripts/tpcc_app.py'), 'run', '--engine', 'candidate',
                   '--commands', str(input_file), '--rebuilt', str(bundle)]
        log = out / (label + '.log')
        with log.open('w') as stream:
            completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
        outcomes = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
        require(bool(outcomes), 'Missing runner result: ' + label)
        run = Path(outcomes[-1]['evidence']); report['runs'][label] = str(run); save(out / 'run.json', report)
        require(completed.returncode == 0 and outcomes[-1]['status'] == 'passed', 'Execution failed: ' + label)
        record = read(run / 'run.json')
        require(not record['cleanup_errors'] and not any(record['remaining'].values()), 'Resources remain')
        binding = verify_loaded_classes(run, bundle, require_all=False)
        require(any(r['member'] == MEMBER for r in binding['classes']), 'Target class not loaded')
        save(out / (label + '-class-binding.json'), binding)
        report['actual_business_commands'] += len(read(input_file)['commands'])
        return run / 'custom_candidate'

    try:
        control = execute('search-control', CONTROL, out / 'commands.json')
        mutant = execute('search-mutant', MUTANT, out / 'commands.json')
        result = assess(commands['commands'], control, mutant); save(out / 'search-result.json', result)
        report['search'] = {k: v for k, v in result.items() if k != 'witness'}
        report['search_business_commands'] = report['actual_business_commands']
        if result['outcome'] == 'found':
            save(out / 'counterexample.json', {'version': 1, 'commands': result['witness']})
            control = execute('replay-control', CONTROL, out / 'counterexample.json')
            mutant = execute('replay-mutant', MUTANT, out / 'counterexample.json')
            reproduced = assess(result['witness'], control, mutant); save(out / 'replay-result.json', reproduced)
            for key in ['outcome', 'first_step', 'command_id', 'public_result_differences', 'state_differences']:
                require(reproduced[key] == result[key], 'Counterexample did not reproduce: ' + key)
            report['reproduction'] = 'passed'
        validate_bundle(CONTROL); validate_bundle(MUTANT)
        report['jvm_runs'] = len(report['runs']); report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=repr(error)); raise
    finally:
        save(out / 'run.json', report)
        print(json.dumps({'status': report['status'], 'evidence': str(out), 'error': report.get('error')}), flush=True)


if __name__ == '__main__':
    main()
