#!/usr/bin/env python3
"""Small, explicit intervention experiment: remove one NewOrder stock-wrap branch."""
import difflib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from rebuild_tpcc import validate_bundle, verify_loaded_classes, digest, JDK

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'work/tpcc/rebuilds/c3cb2330433f'
MEMBER = 'reversedb/tpcc/NewOrderService.class'
SOURCE = 'sources/candidate/reversedb/tpcc/NewOrderService.java'
QUANTITIES = [5, 10, 1, 1]
IDS = ['prepare-21', 'above-11', 'boundary-10', 'below-9']


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def read(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stock(observation, when):
    return next(row for row in observation[when]['stock'] if row['s_i_id'] == 4)


def assess(control, mutant):
    """Check the planned counterexample, rather than counting every mismatch as success."""
    control, mutant = Path(control), Path(mutant)
    before = [read(p / 'initial-state.json') for p in [control, mutant]]
    require(before[0] == before[1], 'Different initial states')
    responses = [read(p / 'responses.json') for p in [control, mutant]]
    observations = [read(p / 'observations.json') for p in [control, mutant]]
    for rows in responses:
        require([r['id'] for r in rows] == IDS, 'Wrong command order')
        require(all(r['status'] == 'committed' for r in rows), 'Unexpected transaction failure')
    require([r['result'] for r in responses[0]] == [r['result'] for r in responses[1]], 'Order result changed')
    require(observations[0][:3] == observations[1][:3], 'Inactive controls changed')
    require(responses[0][:3] == responses[1][:3], 'Inactive control responses changed')
    expected_after = [[21, 11, 10, 100], [21, 11, 10, 9]]
    for i, rows in enumerate(observations):
        require([r['id'] for r in rows] == IDS, 'Wrong observation order')
        require([stock(r, 'before')['s_quantity'] for r in rows] == [26, 21, 11, 10], 'Unexpected pre-stock')
        require([stock(r, 'after')['s_quantity'] for r in rows] == expected_after[i], 'Unexpected post-stock')
        require([stock(r, 'after')['s_ytd'] for r in rows] == [5, 15, 16, 17], 'Quantity accumulator changed')
        require([stock(r, 'after')['s_order_cnt'] for r in rows] == [1, 2, 3, 4], 'Order accumulator changed')
    # The only observed state difference must be this one field in the final command.
    normalized = json.loads(json.dumps(observations[1]))
    stock(normalized[3], 'after')['s_quantity'] = 100
    require(observations[0] == normalized, 'Unexpected collateral state changes')
    finals = [read(p / 'final-state.json') for p in [control, mutant]]
    changed = [table for table in finals[0] if finals[0][table] != finals[1][table]]
    require(set(finals[0]) == set(finals[1]) and changed == ['stock'], 'Unexpected final table differences')
    require(finals[0]['stock']['rows'] == finals[1]['stock']['rows'], 'Stock row count changed')
    invariants = [read(p / 'invariants.json') for p in [control, mutant]]
    require(invariants[0] == invariants[1] and len(invariants[0]) == 5 and not any(invariants[0].values()), 'Unexpected consistency result')
    return {'status': 'passed', 'intervention_detected': True,
            'controls_equal': 3, 'first_difference': IDS[3],
            'control_quantities': expected_after[0], 'mutant_quantities': expected_after[1],
            'order_results_equal': True, 'generic_invariants_detect_mutation': False,
            'state_observations_detect_mutation': True, 'changed_final_tables': changed,
            'scope': 'One manually chosen branch and one deterministic four-order sequence; no automatic rule discovery or general equivalence claim'}


def main():
    validate_bundle(BASE)
    out = ROOT / 'work/tpcc/branch-probes' / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    shutil.copy2(__file__, out / 'workflow.py')
    report = {'status': 'running', 'parent_bundle': str(BASE), 'runs': {}}
    try:
        mutant = out / 'mutant'; shutil.copytree(BASE, mutant)
        shutil.copy2(mutant / 'build.json', mutant / 'parent-build.json')
        build = read(mutant / 'build.json')
        original = (mutant / SOURCE).read_text()
        branch = '            if (var41 < 10) {\n               var41 += 91;\n            }'
        require(original.count(branch) == 1, 'Expected unique pinned branch')
        # Preserve line count so nested-class debug line tables are unchanged.
        modified = original.replace(branch, '            // EXPERIMENT ONLY: skip stock wrap.\n'
                                    '            // The conditional adjustment is omitted.\n'
                                    '            // Preserve source line positions below.')
        (mutant / SOURCE).write_text(modified)
        (out / 'mutation.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='control/' + SOURCE, tofile='mutant/' + SOURCE)))
        command = [str(JDK / 'javac'), '--release', '23', '-proc:none', '-implicit:none',
                   '-sourcepath', str(mutant / 'empty-sourcepath'), '-cp', str(mutant / 'candidate-classes'),
                   '-d', str(mutant / 'candidate-classes'), str(mutant / SOURCE)]
        with (out / 'compile-mutant.log').open('w') as log:
            subprocess.run(command, check=True, timeout=120, stdout=log, stderr=subprocess.STDOUT)
        changed_files = [p for p, h in build['runtime_files'].items() if digest(mutant / p) != h]
        require(changed_files == ['candidate-classes/' + MEMBER], 'Mutation changed unexpected runtime files')
        build.update(status='passed', scope='DELIBERATE EXPERIMENTAL MUTATION: omit NewOrder stock wrap; not an unchanged decompilation build',
                     script_sha256=digest(__file__), parent_build_sha256=digest(mutant / 'parent-build.json'),
                     commands=[command], mutation={'target': SOURCE, 'operation': 'omit conditional +91 adjustment'})
        build['sources'][SOURCE] = digest(mutant / SOURCE)
        build['runtime_files']['candidate-classes/' + MEMBER] = digest(mutant / 'candidate-classes' / MEMBER)
        for row in build['rebuilt_classes']:
            if row['engine'] == 'candidate' and row['member'] == MEMBER:
                row['sha256'] = digest(mutant / 'candidate-classes' / MEMBER)
        save(mutant / 'build.json', build); validate_bundle(mutant)
        commands = []
        for i, quantity in enumerate(QUANTITIES):
            commands.append(dict(id=IDS[i], type='NewOrder', terminal=1, warehouse=1,
                                 district=1, customer=1, clock_ms=1793750400000 + i * 1000,
                                 lines=[dict(item=item, quantity=quantity if item == 4 else 1) for item in [1, 2, 3, 4, 5]]))
        save(out / 'commands.json', {'version': 1, 'commands': commands})
        save(out / 'experiment.json', {
            'hypothesis': 'Omitting stock wrap leaves remaining>=10 cases unchanged but changes remaining=9 from 100 to 9',
            'selection': 'Manual, based on pinned code and existing stock snapshot; not inferred automatically',
            'controls': IDS[:3], 'trigger': IDS[3], 'initial_stock_item_4': 26,
            'changed_runtime_files': changed_files, 'only_changed_source': SOURCE,
            'source_before_sha256': digest(BASE / SOURCE), 'source_after_sha256': digest(mutant / SOURCE),
            'commands_sha256': digest(out / 'commands.json'), 'script_sha256': digest(__file__)})
        for label, bundle in [('control', BASE), ('mutant', mutant)]:
            print('Executing ' + label + ' (four NewOrder commands)', flush=True)
            command = [sys.executable, str(ROOT / 'scripts/tpcc_app.py'), 'run', '--engine', 'candidate',
                       '--commands', str(out / 'commands.json'), '--rebuilt', str(bundle)]
            log = out / (label + '.log')
            with log.open('w') as stream:
                result = subprocess.run(command, timeout=300, stdout=stream, stderr=subprocess.STDOUT)
            outcomes = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
            require(bool(outcomes), 'Runner did not produce a result; see ' + str(log))
            run = Path(outcomes[-1]['evidence']); report['runs'][label] = str(run)
            save(out / 'run.json', report)
            require(result.returncode == 0 and outcomes[-1]['status'] == 'passed', 'Runner failed: ' + label)
            record = read(run / 'run.json')
            require(not record['cleanup_errors'] and not any(record['remaining'].values()), 'Resources remain')
            binding = verify_loaded_classes(run, bundle, require_all=False)
            require(any(row['member'] == MEMBER for row in binding['classes']), 'Target class not loaded')
            save(out / (label + '-class-binding.json'), binding)
        report['assessment'] = assess(Path(report['runs']['control']) / 'custom_candidate', Path(report['runs']['mutant']) / 'custom_candidate')
        validate_bundle(BASE); validate_bundle(mutant)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        save(out / 'run.json', report)
        print(json.dumps({'status': report['status'], 'evidence': str(out), 'error': report.get('error')}), flush=True)


if __name__ == '__main__':
    main()
