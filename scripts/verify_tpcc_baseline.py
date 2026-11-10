#!/usr/bin/env python3
"""Review a completed functional run; retain the original run report unchanged."""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

TYPES = ['NewOrder', 'Payment', 'OrderStatus', 'Delivery', 'StockLevel']
INVARIANTS = {'new_order_gaps', 'new_order_without_order', 'order_line_count_mismatches',
              'district_next_order_mismatches', 'warehouse_district_ytd_mismatches'}


def verify(directory):
    directory = Path(directory)
    hashes = {}

    def read(name, parse=True):
        raw = (directory / name).read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw) if parse else raw.decode()

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    run = read('run.json')
    require(run['status'] == 'execution_passed_pending_coverage_review', 'Execution did not pass')
    require(run['cleanup_errors'] == [] and
            run['remaining'] == dict.fromkeys(['container', 'volume', 'network'], ''), 'Cleanup incomplete')
    hist = read('histograms.json')
    counts = {}
    for category in ['completed', 'aborted', 'rejected', 'unexpected']:
        values = hist[category]['HISTOGRAM']
        require(all(type(v) is int and v >= 0 for v in values.values()), 'Invalid histogram count')
        require(sum(values.values()) == hist[category]['NUM_SAMPLES'], 'Histogram total mismatch')
        counts[category] = values
    expected = {f'com.oltpbenchmark.benchmarks.tpcc.procedures.{name}/{i:02d}': name
                for i, name in enumerate(TYPES, 1)}
    require(set(counts['completed']) == set(expected) and all(counts['completed'].values()),
            'Missing successful transaction coverage')
    require(not any(counts['unexpected'].values()), 'Unexpected SQL errors')
    require(all(set(values) <= set(expected) for values in counts.values()), 'Unknown transaction type')
    require(set(counts['aborted']) <= {next(iter(expected))}, 'Unexpected user abort type')
    for phase in ['before', 'after']:
        invariants = read(f'invariants-{phase}-workload.json')
        require(set(invariants) == INVARIANTS and
                all(type(v) is int and v == 0 for v in invariants.values()), 'Invariant failed or missing')
    atomicity = read('atomicity.json')
    require(atomicity['status'] == 'passed' and atomicity['dirty_read_absent'] is True and
            atomicity['rollback_restored_write_set'] is True, 'Atomicity probe failed')
    require(atomicity['before'] == atomicity['after_rollback'], 'Rollback snapshot mismatch')
    require(atomicity['inside_aborted_transaction'] != atomicity['before'], 'No intermediate write evidence')
    loaded, final = read('counts-loaded.json'), read('counts-final.json')
    completed = {name: counts['completed'][key] for key, name in expected.items()}
    require(final['oorder'] - loaded['oorder'] == completed['NewOrder'] + 1,
            'Order delta inconsistent with workload plus controlled commit')
    require(final['history'] - loaded['history'] == completed['Payment'], 'Payment history delta mismatch')
    log = read('postgres.log', parse=False)
    sessions = collections.defaultdict(collections.Counter)
    for line in log.splitlines():
        match = re.search(r'session=(\S+).*app=tpcc-functional .*execute .*: (BEGIN|COMMIT|ROLLBACK)$', line)
        if match:
            sessions[match[1]][match[2]] += 1
    require(len(sessions) == 4 and all(v['COMMIT'] > 0 for v in sessions.values()), 'Four active sessions not observed')
    require(all(v['BEGIN'] == v['COMMIT'] + v['ROLLBACK'] for v in sessions.values()), 'Unclosed transaction')
    require(sum(v['COMMIT'] for v in sessions.values()) == sum(completed.values()), 'Commit total mismatch')
    require(sum(v['ROLLBACK'] for v in sessions.values()) ==
            sum(counts['aborted'].values()) + sum(counts['rejected'].values()), 'Rollback total mismatch')
    errors = [line for line in log.splitlines() if 'ERROR:' in line]
    require(len(errors) == sum(counts['rejected'].values()) and
            all('could not serialize access' in line for line in errors), 'Unexplained database errors')
    return {'status': 'passed', 'scope': 'Pinned functional baseline only; not replica acceptance or TPC-C certification',
            'completed': completed, 'aborted': hist['aborted']['NUM_SAMPLES'],
            'retries': hist['rejected']['NUM_SAMPLES'], 'unexpected': 0,
            'sessions': dict(sessions), 'invariants_before_and_after': 'all five zero',
            'atomicity': 'passed', 'cleanup': 'passed', 'input_sha256': hashes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    result['reviewer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.directory / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
