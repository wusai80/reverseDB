#!/usr/bin/env python3
"""Read-only extraction of four frozen NewOrder examples; no module inference or pruning."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'evidence/tpcc-observation/20261103-8729d2283fdc'
OUT = ROOT / 'evidence/tpcc-module-observation/20261103-neworder'
SELECTED = {'new-01': 'ordinary_success', 'new-05': 'duplicate_item_success',
            'new-07': 'middle_invalid_item_abort', 'new-14': 'invalid_quantity_rejection'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    locked = json.loads((ARCHIVE / 'manifest.json').read_text())
    inputs = {}
    def read(relative):
        path = ARCHIVE / relative
        if digest(path) != locked[relative]:
            raise ValueError('Frozen evidence changed: ' + relative)
        inputs[str(path.relative_to(ROOT))] = digest(path)
        return json.loads(path.read_text())
    case = 'run/holdout_candidate/'
    traces = {r['id']: r for r in read(case + 'jdbc-trace.json')}
    responses = {r['id']: r for r in read(case + 'responses.json')}
    observations = {r['id']: r for r in read(case + 'observations.json')}
    runtime = {r['id']: r for r in read(case + 'runtime.json')}
    commands = {r['id']: r for r in read('run/inputs/app/tpcc/scenarios/holdout.json')['commands']}
    anchors = {
        'NewOrderService.java': {'preconditions': 34, 'order_header': 39, 'line_and_stock_batches': 50, 'item_price': 55, 'stock_attributes': 62, 'stock_rule': 68, 'execute_batches': 78},
        'CandidateApp.java': {'result_queries': 9, 'neworder_dispatch': 16},
        'AppRuntime.java': {'validate': 37, 'fixture_observer': 76, 'isolation': 101, 'input_rejection': 107, 'commit': 133, 'rollback': 136, 'post_observation': 143},
        'Observation.java': {'neworder_observer': 14}}
    sources = {}
    for name, positions in anchors.items():
        relative = 'run/inputs/app/tpcc/src/reversedb/tpcc/' + name
        path = ARCHIVE / relative
        if digest(path) != locked[relative]:
            raise ValueError('Frozen source changed')
        current = ROOT / 'app/tpcc/src/reversedb/tpcc' / name
        if current.read_bytes() != path.read_bytes():
            raise ValueError('Current source does not match observed source')
        inputs[str(path.relative_to(ROOT))] = digest(path)
        lines = path.read_text().splitlines()
        sources[name] = {'frozen_path': str(path.relative_to(ROOT)), 'sha256': digest(path),
                         'anchors': {label: {'line': n, 'text': lines[n-1]} for label, n in positions.items()}}
    facts, excerpts = {}, {}
    for cid, meaning in SELECTED.items():
        response = responses[cid]
        events = traces.get(cid, {}).get('events', [])
        operations = [{'event_index_zero_based': i, **e} for i, e in enumerate(events)
                      if e['op'].startswith('execute') or e['op'] in ['commit', 'rollback']]
        observation = observations.get(cid)
        fact = {'selected_as': meaning, 'status': response['status'], 'business_operations': operations,
                'execute_batches': sum(e['op'] == 'executeBatch' for e in events),
                'transaction_boundaries': [e['op'] for e in events if e['op'] in ['commit', 'rollback']],
                'has_business_trace': cid in traces, 'has_observation': observation is not None}
        if observation:
            fact['observed_state_unchanged'] = observation['before'] == observation['after']
            fact['row_count_deltas'] = {key: len(observation['after'][key])-len(rows)
                                       for key, rows in observation['before'].items()}
            fact['stock_selected_fields'] = {when: [{k: row[k] for k in ['s_i_id', 's_quantity', 's_ytd', 's_order_cnt']}
                                                   for row in observation[when]['stock']]
                                            for when in ['before', 'after']}
        facts[cid] = fact
        excerpts[cid] = {'command': commands[cid], 'response': response, 'trace': traces.get(cid),
                         'observation': observation, 'runtime': runtime.get(cid)}
    for cid in ['new-01', 'new-05']:
        ops = facts[cid]['business_operations']
        assert facts[cid]['transaction_boundaries'] == ['commit']
        batches = [i for i, e in enumerate(ops) if e['op'] == 'executeBatch']
        assert len(batches) == 2
        assert [e['op'] for e in ops[batches[-1]+1:]] == ['executeQuery', 'executeQuery', 'commit']
    assert facts['new-07']['execute_batches'] == 0
    assert facts['new-07']['transaction_boundaries'] == ['rollback']
    assert facts['new-07']['observed_state_unchanged']
    assert facts['new-14']['status'] == 'rejected' and not facts['new-14']['has_business_trace']
    OUT.mkdir(parents=True, exist_ok=False)
    save(OUT / 'observations.json', {'method': 'Offline extraction from frozen observations plus manually selected source anchors',
          'scope': 'Four selected NewOrder examples; not exhaustive capability coverage', 'cases': facts, 'sources': sources,
          'limits': ['No new database execution', 'No complete dynamic call graph', 'No inference that unobserved data or modules are unnecessary',
                     'Logical module boundaries are human proposals in the accompanying report',
                     'State observer is fixture-specific, including order ID >=3001; not a general capability contract']})
    save(OUT / 'selected-records.json', excerpts)
    save(OUT / 'inputs.json', inputs)
    shutil.copy2(__file__, OUT / 'workflow.py')
    print(json.dumps({'status': 'passed', 'evidence': str(OUT), 'selected_cases': len(facts),
                      'source_snapshots_verified': len(sources), 'database_runs': 0}))


if __name__ == '__main__':
    main()
