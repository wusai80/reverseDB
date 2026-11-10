#!/usr/bin/env python3
"""Build a conservative DAG from the frozen Java extraction, without application execution."""
import hashlib
import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'prototype/module_pruning'))
from call_dag import condense, from_extraction


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-extraction', default='evidence/module-initialization/20261105-9b9711eca669')
    args = parser.parse_args()
    source = (ROOT / args.source_extraction).resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    data = json.loads((source / 'extracted.json').read_text())
    files = sorted({m['location']['file'] for m in data['methods']
                    if m['symbol'].split('#')[0] in {
                        'com.oltpbenchmark.api.TpccApp',
                        'com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus'}})
    root = next(m['symbol'] for m in data['methods'] if m['symbol'] == 'com.oltpbenchmark.api.TpccApp#main(java.lang.String[])')
    inputs = [source / 'extracted.json', source / 'bridge-contract.json', *map(Path, files)]
    for path in inputs:
        if digest(path) != manifest[str(path.relative_to(source))]:
            raise ValueError('Frozen source changed: ' + str(path))
    out = ROOT / 'evidence/module-call-dag' / ('20261105-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    inputs += [source / 'manifest.json', Path(__file__),
               ROOT / 'prototype/module_pruning/call_dag.py',
               ROOT / 'prototype/module_pruning/test_call_dag.py']
    freeze = {}
    for path in inputs:
        relative = path.relative_to(ROOT)
        dest = out / 'inputs' / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        freeze[str(relative)] = digest(dest)
    graph = from_extraction(data, files, [root])
    dag = condense(graph)
    for name, obj in [('graph.json', graph), ('dag.json', dag), ('freeze.json', freeze)]:
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    # DOT retains every component; JSON retains every edge's provenance.
    lines = ['digraph dependencies {', 'rankdir=LR;']
    for component in dag['components']:
        members = component['members']
        label = '\n'.join(members)
        reachable = any(n in dag['reachability_witnesses'] for n in members)
        lines.append(f'c{component["id"]} [label={json.dumps(label)}, color={"black" if reachable else "orange"}];')
    lines.extend(f'c{e["from"]} -> c{e["to"]};' for e in dag['edges'])
    (out / 'dag.dot').write_text('\n'.join(lines + ['}']) + '\n')
    # Small source projection for human review; this is explicitly NOT the full DAG.
    internal = {n['id']: n for n in graph['nodes'] if n['kind'] in ('method', 'sql_field')}
    projected = {n['id']: n for n in graph['nodes'] if n['kind'] in
                 ('method', 'sql_field', 'class_initializer', 'initialized_field')}
    short_ids = {n: 'n' + str(i) for i, n in enumerate(sorted(projected))}
    diagram = ['flowchart LR']
    for symbol, identifier in short_ids.items():
        label = symbol.split('#')[-1].split('(')[0]
        label = symbol.split('#')[0].split('.')[-1] + '.' + label
        diagram.append(f'  {identifier}["{label}"]')
    pairs = sorted({(e['from'], e['to'], e['kind']) for e in graph['edges']
                    if e['from'] in projected and e['to'] in projected})
    for src, dst, kind in pairs:
        diagram.append(f'  {short_ids[src]} -->|{kind}| {short_ids[dst]}')
    outside = [n for n in sorted(internal) if n not in dag['reachability_witnesses']]
    diagram.append('  classDef unresolved fill:#fff1d6,stroke:#b36b00;')
    projected_outside = [n for n in sorted(projected) if n not in dag['reachability_witnesses']]
    if projected_outside:
        diagram.append('  class ' + ','.join(short_ids[n] for n in projected_outside) + ' unresolved;')
    (out / 'source-projection.mmd').write_text('\n'.join(diagram) + '\n')
    with (out / 'tests.log').open('w') as log:
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
            '-s', str(out / 'inputs/prototype/module_pruning'), '-p', 'test_call_dag.py', '-v'],
            stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('DAG checks failed; see ' + str(out / 'tests.log'))
    # Evidence that the discarded display computation is reachable; do not remove its edge.
    display = next(n for n in internal if '#getOrderLines(' in n)
    if display not in dag['reachability_witnesses']:
        raise AssertionError('Lost discarded reflective call dependency')
    report = {'status': 'completed', 'source_extraction': str(source.relative_to(ROOT)),
        'roots': graph['roots'], 'source_files': files,
        'scope': 'Decompiled OrderStatus plus frozen original TpccApp adapter; other bodies are external.',
        'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
        'scc_components': len(dag['components']),
        'cyclic_components': sum(c['cyclic'] for c in dag['components']),
        'internal_method_and_sql_nodes': len(internal), 'outside_internal_nodes': outside,
        'discarded_display_still_reachable': True, 'tests_passed': 6,
        'dependency_summary_complete': False, 'deletion_authorized': False,
        'new_application_runs': 0, 'new_database_runs': 0, 'deletions': 0,
        'limitations': graph['limitations']}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    contents = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(contents, indent=2) + '\n')
    print(json.dumps({'archive': str(out), **{k: report[k] for k in
        ('nodes', 'edges', 'scc_components', 'cyclic_components', 'outside_internal_nodes', 'tests_passed')}}, indent=2))


if __name__ == '__main__':
    main()
