"""Fixed finite development experiment. No performance measurements.

Run from repository root: python3 -m prototype.region_semantics.verify --out DIR
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import unittest

from .fixtures import programs
from .model import Contract, MIN_I64, MAX_I64, observe
from .rewrite import check, discover_regions, propose
from .sqlite_backend import execute
from .test_semantics import SemanticsTests


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def states():
    # Complete enumeration for two variable keys; fixed untouched sentinel.
    for values in itertools.product([None, -1, 0, 1], repeat=2):
        yield {**{k:v for k,v in enumerate(values) if v is not None}, 999:123}


def inputs():
    for key, other, value, enabled in itertools.product([0,1], [0,1], [-1,0,1], [False,True]):
        yield dict(key=key, other=other, value=value, enabled=enabled)


def fault_plans(program, contract):
    sites = [o['id'] for o in program['ops'] if o['op'] == 'write' or o['op'] == 'read' and contract.read_errors_observable]
    for bits in itertools.product([False, True], repeat=len(sites)):
        yield {site:'fault:'+site for site, enabled in zip(sites,bits) if enabled}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    files = sorted(Path(__file__).parent.glob('*.py'))
    snapshots = args.out/'inputs'; snapshots.mkdir()
    hashes = {}
    for path in files:
        shutil.copyfile(path, snapshots/path.name)
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(args.out/'manifest.json', dict(schema='bounded-semantics-evidence/v1', sources=hashes,
          python=platform.python_version(), sqlite=sqlite3.sqlite_version,
          purpose='finite development validation; no new application, machine proof, or performance evidence'))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SemanticsTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write(args.out/'unit-tests.json', dict(tests=result.testsRun, failures=[str(x) for x in result.failures], errors=[str(x) for x in result.errors]))
    if not result.wasSuccessful(): raise SystemExit(1)
    rows, artifacts, mismatches = [], {}, []
    for name, source in programs().items():
        for strict in [True, False]:
            contract = Contract(strict); key = name+('/strict' if strict else '/successful-read')
            candidate, cert = propose(source, contract)
            admission = check(source, candidate, cert, contract)
            if not admission['accepted']: raise AssertionError(admission)
            stats = dict(cases=0, sqlite_executions=0, reference_executions=0, source_reads=0, candidate_reads=0, source_packets=0, candidate_packets=0)
            configs = [(i,s) for i,s in itertools.product(inputs(),states())]
            configs += [(dict(key=0,other=1,value=v,enabled=e), {0:MIN_I64,1:MAX_I64,999:123}) for v,e in itertools.product([MIN_I64,MAX_I64],[False,True])]
            for input_values, state in configs:
                for faults in fault_plans(source, contract):
                    # Deleted reads cannot have a legal fault under this contract.
                    before = observe(source, input_values, state, faults, contract)
                    after = observe(candidate, input_values, state, faults, contract)
                    sql_before = execute(source, input_values, state, faults, contract)
                    sql_after = execute(candidate, input_values, state, faults, contract)
                    stats['cases'] += 1; stats['sqlite_executions'] += 2; stats['reference_executions'] += 2
                    for arm, data in [('source',before), ('candidate',after)]:
                        stats[arm+'_reads'] += data['work']['reads']; stats[arm+'_packets'] += data['work']['packets']
                    # Python equality treats True == 1; typed observations must not.
                    observations = [json.dumps(r['observation'], sort_keys=True) for r in (before, after, sql_before, sql_after)]
                    if len(set(observations)) != 1 or before['work'] != sql_before['work'] or after['work'] != sql_after['work']:
                        mismatches.append(dict(case=key, inputs=input_values, state=state, faults=faults, before=before, after=after, sql_before=sql_before, sql_after=sql_after))
            rows.append(dict(case=key, **stats))
            artifacts[key] = dict(source=source, candidate=candidate, certificate=cert, admission=admission,
                regions=discover_regions(source), local_regions=discover_regions(source,local_only=True))
    write(args.out/'cases.json', artifacts)
    write(args.out/'mismatches.json', mismatches)
    summary = dict(status='pass' if not mismatches else 'fail', unit_tests=result.testsRun, programs=len(programs()), contracts=2,
        configurations_per_program_contract=388, bounded_states=16, bounded_inputs=24, extra_boundary_configurations=4,
        fault_matrix='all subsets of eligible DB sites, distinct stable error code per site',
        total_cases=sum(r['cases'] for r in rows), sqlite_executions=sum(r['sqlite_executions'] for r in rows),
        reference_executions=sum(r['reference_executions'] for r in rows), mismatches=len(mismatches), rows=rows,
        scope='finite checks for constructed IR examples; all store rows compared; no timing or application transfer')
    write(args.out/'summary.json', summary)
    print(json.dumps({k:v for k,v in summary.items() if k != 'rows'}, indent=2))
    if mismatches: raise SystemExit(1)


if __name__ == '__main__': main()
