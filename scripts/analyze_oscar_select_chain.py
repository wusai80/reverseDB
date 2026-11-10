#!/usr/bin/env python3
"""Offline audit and mapping counterexamples for one frozen SELECT profile."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'


def main():
    folder = Path(sys.argv[1]).resolve()
    out = folder.with_name(folder.name + '-analysis-' + uuid4().hex[:8])
    out.mkdir(exist_ok=False)
    load = lambda p: json.loads(p.read_text())
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    checks = []
    def check(name, ok):
        assert ok, name
        checks.append(name)
    def save(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    shutil.copy2(__file__, out / 'analyze_oscar_select_chain.py')
    manifest = load(folder / 'manifest.json')
    check('all frozen run artifacts intact', all(sha(folder / p) == h for p, h in manifest.items()))
    report = load(folder / 'run.json')
    check('source inputs still bound', all(sha(Path(p)) == h for p, h in report['inputs'].items()))
    check('successful original run and resource cleanup', report['status'] == 'passed' and not report['cleanup_errors'])
    records = load(folder / 'replay/construction-records.json')
    codes = load(folder / 'replay/construction-codes.json')
    analysis = load(folder / 'replay/select-analysis.json')
    selects = [r for r in records if r['kind'] == 'select_compile']
    filters = [r for r in records if r['kind'] == 'build_filter']
    check('114 original SELECTs, 149 disjoint filter roots', len(selects) == 114 and len(filters) == 149)
    for r in records:
        check('valid profile refs / self CPU ' + str(len(checks)),
              all(f['code'].startswith('native:') or f['code'] in codes for f in r['functions'])
              and abs(sum(f['self_cpu_ms'] for f in r['functions']) - r['self_cpu_ms']) < 1e-8)
    # Recompute reachability, including shared ancestors, from raw clone edges.
    edges = load(folder / 'replay/query-clones.json')
    children = {}
    for e in edges:
        children.setdefault(e['source'], set()).add(e['target'])
    for r in filters:
        seen, pending = {r['query']}, [r['query']]
        while pending:
            for child in children.get(pending.pop(), set()):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        actual = [i for i, s in enumerate(selects) if s['query'] in seen]
        check('filter provenance ' + str(len(checks)), r['select_consumers'] == actual)
    points = [r for r in selects if r['facts']['integer_pk_point_candidate']]
    check('point compile cost independently recomputed', len(points) == 46 and
          abs(sum(r['self_cpu_ms'] for r in points) - analysis['point_compile_cpu_ms']) < 1e-8)
    check('compile cost independently recomputed', abs(sum(r['self_cpu_ms'] for r in selects) - analysis['compile_cpu_ms']) < 1e-8)
    check('48 original compiler boundary checks retained', len(load(folder / 'replay/shape-boundaries.json')) == 48)
    base = ROOT / 'evidence/oscar-baseline/20261106-ed9f704d86d6/a'
    check('full original database and returned objects match',
          load(folder / 'replay/after-full-state.json') == load(base / 'after-full-state.json') and
          load(folder / 'replay/result.json')['orders'] == load(base / 'result.json')['orders'])

    # An observed subtree budget is not an eliminable-time estimate. Retain
    # cold construction in a future experiment; only later same-shape uses
    # would be candidates for template binding.
    cold, repeat, seen_shapes = 0.0, 0.0, set()
    point_functions = Counter()
    for r in points:
        key = json.dumps([r['sql'], r['facts']['result_abi']], sort_keys=True)
        if key in seen_shapes:
            repeat += r['self_cpu_ms']
        else:
            cold += r['self_cpu_ms']
            seen_shapes.add(key)
        for f in r['functions']:
            if f['code'] in codes:
                point_functions[codes[f['code']]['name']] += f['calls']

    # Two independent original-compiler counterexamples to an underspecified
    # cache key. No candidate, no database connection, no query evaluation.
    sys.path[:0] = [str(ROOT / 'prototype/oscar'), str(SOURCE / 'src'), str(SOURCE / 'sandbox')]
    os.environ.update(DJANGO_SETTINGS_MODULE='settings', DATABASE_ENGINE='django.db.backends.postgresql',
                      DATABASE_NAME='offline_no_database', DATABASE_HOST='127.0.0.1', DATABASE_PORT='1')
    sys.argv = [sys.argv[0], 'offline', str(out)]
    from django.db.backends.postgresql.base import DatabaseWrapper
    with patch.object(DatabaseWrapper, 'get_new_connection', side_effect=AssertionError('Offline analysis attempted DB access')):
        import django
        django.setup()
        from django.db import connection
        from oscar.core.loading import get_model
        from select_chain_probe import abi
        selected = None
        for g in analysis['shapes']:
            if g['point_candidate']:
                model = get_model(*g['model'].split('.'))
                fields = [s['expression']['field'] for s in g['result_abi']['select']]
                if len(fields) < len(model._meta.concrete_fields):
                    selected = model, fields
                    break
        assert selected is not None
        model, fields = selected
        def compile_original(qs):
            qs = qs.filter(pk=314159).order_by()
            qs.query.set_limits(high=21)
            compiler = qs.query.get_compiler(using='default')
            sql, params = compiler.as_sql()
            return dict(sql=sql, params=list(params), result_abi=abi(compiler), iterable=qs._iterable_class.__qualname__)
        full = compile_original(model._base_manager.all())
        partial = compile_original(model._base_manager.only(*fields))
        values = compile_original(model._base_manager.values(*fields))
        values_list = compile_original(model._base_manager.values_list(*fields))
        save('mapping-counterexamples.json', dict(model=model._meta.label, full=full, partial=partial,
                                                  values=values, values_list=values_list))
        check('same model / PK but different projection requires different plan',
              full['sql'] != partial['sql'] and full['result_abi'] != partial['result_abi'])
        check('identical SQL / params / compiler ABI but different consumer',
              values['sql'] == values_list['sql'] and values['params'] == values_list['params'] and
              values['result_abi'] == values_list['result_abi'] and values['iterable'] != values_list['iterable'])
        check('mapping counterexamples opened no database connection', connection.connection is None)

    save('candidate-contract.json', dict(
        status='prioritized research candidate; no generated residual or admitted backend',
        equation='Compile(S, v) = Bind(Template(S), Validate_S(v)) only under explicit structural/effect guards',
        static_key=['framework/backend code and schema metadata', 'model and ordered projected fields',
                    'integer exact lookup and its field domain', 'query structure, LIMIT and ordering/lock flags',
                    'result select/klass_info/annotation map and consumer kind'],
        dynamic_retained=['original value preparation and DB adaptation', 'integer field range / EmptyResultSet',
                          'NULL structure change or original fallback', 'original errors',
                          'each real database read in original transaction order',
                          'result conversion and fresh model construction', 'signals, related-object aliases, deferred fields'],
        exclusions=['custom managers/lookups/fields/converters without closed effects',
                    'schema or lookup-registry changes without invalidation', 'join/aggregate/lock variants',
                    'any row/result/model-instance caching'],
        observed_scope=dict(shapes=6, calls=46, cold_compilations=6, later_same_shape_calls=40,
                            compile_cpu_ms=analysis['point_compile_cpu_ms'],
                            share_of_select_compile_cpu=analysis['point_share_of_observed_compile_cpu'],
                            cold_compile_cpu_ms=cold, later_compile_cpu_ms=repeat,
                            point_filter_cpu_ms=analysis['point_only_filter_cpu_ms'],
                            compile_function_calls=dict(point_functions)),
        interpretation='Observed diagnostic budgets, not a proof all work is removable or a predicted end-to-end speedup',
        shared_algorithm='Original DP unchanged. Its DB-call objective does not score host-only compilation reuse; no claim of a new DP-selected region',
        next='One generic shape-plus-result-ABI residual, checked across these model shapes with original dynamic checks; no project-specific SQL rewrite'))
    save('audit.json', dict(status='passed', checks=checks, input_manifest_sha256=sha(folder / 'manifest.json'),
                           frozen_artifact_count=len(manifest), database_connections=0,
                           algorithm_changed=False, performance_claim=None))
    save('manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps(dict(status='passed', output=str(out), checks=len(checks))))


if __name__ == '__main__':
    main()
