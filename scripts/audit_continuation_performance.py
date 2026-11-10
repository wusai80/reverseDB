"""Read-only, independent audit of the fixed continuation performance study.

Run after measurement. Writes its report beside (never inside) frozen evidence.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def encode(value):
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, (list, tuple)):
        return [encode(x) for x in value]
    return value


def snapshot(path):
    # Open SQLite read-only; do not import or execute the benchmark implementation.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        schema = encode(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall())
        tables = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            tables[name] = sorted([encode(row) for row in db.execute('SELECT * FROM ' + quoted)],
                                  key=lambda x: json.dumps(x, ensure_ascii=False))
    return dict(schema=schema, tables=tables)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    out = args.evidence.resolve()
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    protocol = load(out / 'protocol.json')
    runs = load(out / 'runs.json')
    summary = load(out / 'summary.json')
    check('successful report without failure record', load(out / 'report.json')['status'] == 'pass' and not (out / 'failure.json').exists())
    manifest = load(out / 'manifest.json')
    check('exact evidence inventory', set(manifest) == {str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() and p.name != 'manifest.json'})
    check('all evidence bytes match manifest', all(sha(out / p) == h for p, h in manifest.items()))
    for filename in ['input-hashes.json', 'media-hashes.json', 'baseline-hashes.json']:
        check(filename + ' unchanged', all(sha(ROOT / p) == h for p, h in load(out / filename).items()))
    check('copied source matches recorded input', all(sha(out / 'inputs' / p) == h for p, h in load(out / 'input-hashes.json').items()))
    check('54 unique scheduled executions in exact order', len(runs) == 54 and len({r['id'] for r in runs}) == 54 and [r['id'] for r in runs] == [t['id'] for t in protocol['schedule']])
    check('48 formal and six diagnostic executions', sum(r['stage'] == 'formal' for r in runs) == 48 and sum(r['stage'] == 'diagnostic' for r in runs) == 6)
    states = {n: load(out / f'state-{n}.json') for n in [100, 5000]}
    golden = {}
    for record, trial in zip(runs, protocol['schedule']):
        name = record['id']
        folder = out / name
        scenario = record['scenario']
        size = 5000 if scenario == 'export-5000' else 100
        check(name + ' metadata and persisted result', all(record[k] == v for k, v in trial.items()) and record == load(folder / 'result.json'))
        check(name + ' full database state and schema', snapshot(folder / 'library.db') == states[size] and record['state_sha256'] == digest(states[size]))
        check(name + ' warmup full state', snapshot(folder / 'warmup.db') == states[100])
        output = folder / 'output.json'
        check(name + ' output hash, bytes and row count', sha(output) == record['output_sha256'] and output.stat().st_size == record['output_bytes'] and len(load(output)) == size == record['rows'])
        golden.setdefault(scenario, output.read_bytes())
        check(name + ' exact cross-arm output bytes', output.read_bytes() == golden[scenario])
        expected_faults = [] if size == 5000 else [2, 22, 42, 62, 82] if scenario == 'scan-sparse' else [i for i in range(1, 101) if i % 10 != 1]
        check(name + ' exact fault schedule', record['fault_ids'] == expected_faults)
        check(name + ' valid wall and CPU timing', all(math.isfinite(record[k]) and record[k] > 0 for k in ['wall_ms', 'process_cpu_ms']))
        if record['stage'] == 'formal':
            check(name + ' observers absent', record['diagnostics'] is None and record['queries_sha256'] is None)
        else:
            check(name + ' query trace hash', digest(load(folder / 'queries.json')) == record['queries_sha256'])
    recomputed = {}
    for scenario in summary:
        diagnostic = {r['arm']: r for r in runs if r['scenario'] == scenario and r['stage'] == 'diagnostic'}
        original, candidate = diagnostic['original'], diagnostic['candidate']
        check(scenario + ' identical full SQL traces', load(out / original['id'] / 'queries.json') == load(out / candidate['id'] / 'queries.json'))
        expected_regions, expected_materializations = (4500, 0) if scenario == 'export-5000' else (100, 5 if scenario == 'scan-sparse' else 90)
        check(scenario + ' observed candidate mechanism', candidate['diagnostics']['regions'] == expected_regions and candidate['diagnostics']['materializations'] == expected_materializations and candidate['diagnostics']['demand_compilations'] == 2 * expected_regions)
        pairs = []
        for block in range(8):
            group = [r for r in runs if r['scenario'] == scenario and r['stage'] == 'formal' and r['block'] == block]
            expected_order = ['original', 'candidate'] if block % 2 == 0 else ['candidate', 'original']
            check(scenario + f' block {block} counterbalanced pair', [r['arm'] for r in group] == expected_order)
            arms = {r['arm']: r for r in group}
            a, c = arms['original']['wall_ms'], arms['candidate']['wall_ms']
            pairs.append(dict(block=block, original_ms=a, candidate_ms=c, speedup=a / c))
        check(scenario + ' complete raw pairs', pairs == summary[scenario]['pairs'])
        logs = [math.log(p['speedup']) for p in pairs]
        medians = {arm: statistics.median(p[arm + '_ms'] for p in pairs) for arm in ['original', 'candidate']}
        rng = random.Random(protocol['seed'])
        samples = sorted(math.exp(sum(rng.choices(logs, k=8)) / 8) for _ in range(20000))
        recalculated = dict(median_ms=medians, paired_geometric_speedup=math.exp(sum(logs) / 8),
                            paired_median_speedup=statistics.median(p['speedup'] for p in pairs),
                            candidate_latency_change_percent=100 * (medians['candidate'] / medians['original'] - 1),
                            candidate_wins=sum(p['speedup'] > 1 for p in pairs), paired_bootstrap95=[samples[499], samples[19499]])
        check(scenario + ' independently recomputed summaries', all(
            math.isclose(recalculated[k], summary[scenario][k], rel_tol=1e-12) for k in
            ['paired_geometric_speedup', 'paired_median_speedup', 'candidate_latency_change_percent', 'candidate_wins']))
        check(scenario + ' independently recomputed medians and interval', medians == summary[scenario]['median_ms'] and all(math.isclose(a, b, rel_tol=1e-12) for a, b in zip(recalculated['paired_bootstrap95'], summary[scenario]['paired_bootstrap95'])))
        recomputed[scenario] = recalculated
    report = dict(status='pass', checks=checks, checks_passed=len(checks), summaries=recomputed,
                  evidence=str(out.relative_to(ROOT)), evidence_manifest_sha256=sha(out / 'manifest.json'),
                  audit_source_sha256=sha(Path(__file__)), timing_reruns=0)
    target = out.with_name(out.name + '-audit.json')
    with target.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(status='pass', checks_passed=len(checks), report=str(target))))


if __name__ == '__main__':
    main()
