#!/usr/bin/env python3
"""Bounded offline INSERT subgraph test; never opens a database connection."""
from collections import Counter
from contextlib import contextmanager
import copy
import datetime
from decimal import Decimal
import dis
import hashlib
import importlib
import json
import marshal
import os
from pathlib import Path
import random
import re
import shutil
import sys
import types
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'evidence/oscar-module-profile/20261107-a70b2db5616e'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def main():
    assert sys.version_info[:2] == (3, 12), 'Use the pinned Oscar venv interpreter'
    out = ROOT / 'evidence/oscar-insert-shape' / ('20261107-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status='running', checks=[], database_connections=0,
                  optimizer_modified=False, performance_claim=None, inputs={})

    def save(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    def check(label, condition):
        assert condition, label
        report['checks'].append(label)

    try:
        save('protocol.json', dict(
            scope='offline compilation only; one pure single-row INSERT assembly subgraph',
            observed='26 order-line/price/attribute INSERTs from fixed original profile',
            generated='96 deterministic scalar matrices with unseen values and held-out widths',
            seed=20261107,
            controls='expressions, custom placeholders, custom values/hooks, empty/raw/bulk inputs',
            stop='one differential pass; no DB replay, timing, installation or optimizer change'))
        for p in [Path(__file__).resolve(), ROOT / 'prototype/oscar/insert_shape_specialization.py',
                  ROOT / 'prototype/oscar/baseline_probe.py']:
            dest = out / 'inputs' / p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            report['inputs'][str(p)] = sha(p)
        manifest = load(PROFILE / 'manifest.json')
        for rel in ['profiles/executed-code.json', 'profiles/bytecode-witness.json',
                    'profiles/b0-original-profile/events.json',
                    'profiles/b0-original-profile/after-full-state.json',
                    'profiles/b0-original-profile/cpu-profile.json',
                    'profiles/b1-original-profile/cpu-profile.json']:
            check('profile integrity ' + rel, sha(PROFILE / rel) == manifest[rel])
            report['inputs'][str(PROFILE / rel)] = manifest[rel]
        first_manifest = load(FIRST / 'manifest.json')
        for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            rel = 'inputs/scripts/' + name
            check('shared core unchanged ' + name,
                  sha(ROOT / 'scripts' / name) == sha(FIRST / rel) == first_manifest[rel])
        for witness in load(PROFILE / 'profiles/bytecode-witness.json'):
            check('pinned source ' + witness['module'], sha(Path(witness['source'])) == witness['source_sha256'])

        sys.path[:0] = [str(ROOT / 'prototype/oscar'), str(SOURCE / 'src'), str(SOURCE / 'sandbox')]
        os.environ.update(DJANGO_SETTINGS_MODULE='settings', DATABASE_ENGINE='django.db.backends.postgresql',
                          DATABASE_NAME='offline_no_database', DATABASE_HOST='127.0.0.1', DATABASE_PORT='1')
        # baseline_probe exports the canonical code/parameter encoders; no main().
        sys.argv = [sys.argv[0], 'offline', str(out)]
        import baseline_probe as base
        import django
        from django.db.backends.postgresql.base import DatabaseWrapper
        with patch.object(DatabaseWrapper, 'get_new_connection', side_effect=AssertionError('Offline experiment attempted database access')):
            django.setup()
            from django.db import connection
            from django.db.models import CharField, Value, IntegerField
            from django.db.models.sql.subqueries import InsertQuery
            from django.db.models.sql.compiler import SQLInsertCompiler as BaseInsert
            from django.db.backends.postgresql.compiler import SQLInsertCompiler as PgInsert
            from django.db.backends.base.operations import BaseDatabaseOperations
            from django.db.backends.postgresql.psycopg_any import Jsonb
            from oscar.core.loading import get_model
            from insert_shape_specialization import LiteralInsertAssembler

            # Recover the relevant code from retained actual-execution .pyc files.
            targets = {
                'oscar.apps.order.utils': ['OrderCreator.create_line_models', 'OrderCreator.create_line_price_models', 'OrderCreator.create_line_attributes'],
                'django.db.models.sql.compiler': ['SQLInsertCompiler.as_sql', 'SQLInsertCompiler.assemble_as_sql', 'SQLInsertCompiler.field_as_sql', 'SQLInsertCompiler.prepare_value', 'SQLInsertCompiler.pre_save_val'],
                'django.db.backends.postgresql.compiler': ['SQLInsertCompiler.assemble_as_sql'],
                'django.db.backends.base.operations': ['BaseDatabaseOperations.modify_insert_params', 'BaseDatabaseOperations.bulk_insert_sql'],
                'django.db.backends.postgresql.operations': ['DatabaseOperations.bulk_insert_sql'],
            }
            def walk(code):
                yield code
                for const in code.co_consts:
                    if isinstance(const, types.CodeType):
                        yield from walk(const)
            recovered = []
            for module, names in targets.items():
                rel = 'profiles/observed-pyc/' + module + '.pyc'
                p = PROFILE / rel
                check('pyc integrity ' + module, sha(p) == manifest[rel])
                codes = {c.co_qualname: c for c in walk(marshal.loads(p.read_bytes()[16:]))}
                imported = importlib.import_module(module)
                for name in names:
                    code = codes[name]
                    current = imported
                    for part in name.split('.'):
                        current = getattr(current, part)
                    check('live code matches retained pyc ' + module + '.' + name,
                          base.code_hash(code) == base.code_hash(current.__code__))
                    recovered.append(dict(module=module, name=name, code_sha256=base.code_hash(code),
                        instructions=[dict(offset=i.offset, op=i.opname, arg=i.argrepr) for i in dis.get_instructions(code)],
                        exception_table_hex=code.co_exceptiontable.hex()))
            save('bytecode-subgraph.json', recovered)
            save('contract.json', dict(
                formula="A(F,[v]) = ((('%s',) * len(F),), [list(v)])",
                static=['pinned compiler/backend code', 'ordinary field placeholder behavior', 'field count'],
                dynamic=['every prepared parameter value/reference', 'fresh parameter containers'],
                retained=['pre_save_val', 'prepare_value/get_db_prep_save', 'full as_sql outer construction',
                          'model construction/identity/signals', 'driver execute and return conversion', 'transaction/lock/recovery'],
                skipped=['per-field field_as_sql', 'identity modify_insert_params', 'nested zip/generator/per-field parameter lists'],
                guarded=['exact built-in scalar types', 'exact framework fields without get_placeholder',
                         'ordinary single row', 'original field_as_sql and parameter hook'],
                fallback='Call original assembly before any skipped operation on an unsupported input',
                scope='Frozen framework and consumer; no arbitrary monkeypatch/concurrent reconfiguration guarantee',
                derivation='Manual residual of bytecode-audited pure branch, not general automatic decompilation/synthesis',
                identity='All parameter object identities retained; immutable internal placeholder tuples may be shared'))

            models = {get_model('order', n)._meta.db_table: get_model('order', n)
                      for n in ['Line', 'LinePrice', 'LineAttribute']}
            final_tables = load(PROFILE / 'profiles/b0-original-profile/after-full-state.json')['tables']
            def decode(value):
                if isinstance(value, dict):
                    assert value['type'] == 'Decimal'
                    return Decimal(value['value'])
                return value
            code_index = load(PROFILE / 'profiles/executed-code.json')
            events = []
            for event in load(PROFILE / 'profiles/b0-original-profile/events.json'):
                if event['kind'] != 'execute':
                    continue
                match = re.match(r'INSERT INTO "([^"]+)" \((.*?)\) VALUES ', event['sql'])
                if match and match[1] in models and any(code_index[f['code']]['name'] == 'OrderCreator.create_line_models' for f in event['stack']):
                    events.append((event, match[1], re.findall(r'"([^"]+)"', match[2])))
            check('fixed 26 observed inserts selected', len(events) == 26)

            def compiler_for(model, columns, values):
                fields = [{f.column: f for f in model._meta.concrete_fields}[n] for n in columns]
                obj = model(**{f.attname: v for f, v in zip(fields, values)})
                query = InsertQuery(model)
                query.insert_values(fields, [obj], raw=False)
                compiler = query.get_compiler(using='default')
                compiler.returning_fields = model._meta.db_returning_fields
                return compiler

            watched = {f.__code__: name for name, f in [
                ('field_as_sql', BaseInsert.field_as_sql), ('modify_insert_params', BaseDatabaseOperations.modify_insert_params),
                ('prepare_value', BaseInsert.prepare_value), ('pre_save_val', BaseInsert.pre_save_val),
                ('base_assemble', BaseInsert.assemble_as_sql), ('postgres_assemble', PgInsert.assemble_as_sql)]}
            @contextmanager
            def count_calls():
                counts = Counter()
                def observe(frame, event, arg):
                    if event == 'call' and frame.f_code in watched:
                        counts[watched[frame.f_code]] += 1
                assert sys.getprofile() is None
                sys.setprofile(observe)
                try:
                    yield counts
                finally:
                    sys.setprofile(None)

            assembler = LiteralInsertAssembler()
            results, totals = [], {'original': Counter(), 'residual': Counter()}
            def exact_parameters(value):
                if isinstance(value, Jsonb):
                    return dict(type='Jsonb', adapted=base.encode(value.adapted), serialized=value.dumps(value.adapted))
                if isinstance(value, (list, tuple)):
                    return [exact_parameters(v) for v in value]
                return base.encode(value)
            for index, (event, table, columns) in enumerate(events):
                reconstructed = []
                values = []
                for column, value in zip(columns, event['params']):
                    if isinstance(value, dict) and value['type'] == 'django.db.backends.postgresql.psycopg_any.Jsonb':
                        # The old trace retained the adapter type, not its JSON
                        # payload. Recover the input from the full state witness;
                        # do not fabricate a value or call type-only equality full equality.
                        key = dict(zip(columns, event['params']))
                        matches = [r for r in final_tables[table] if
                                   all(r[k] == key[k] for k in ('line_id', 'option_id', 'type'))]
                        assert len(matches) == 1
                        values.append(matches[0][column])
                        reconstructed.append(column)
                    else:
                        values.append(decode(value))
                a = compiler_for(models[table], columns, values)
                b = compiler_for(models[table], columns, values)
                with count_calls() as ca:
                    original = a.as_sql()
                b.assemble_as_sql = types.MethodType(assembler.assemble, b)
                with count_calls() as cb:
                    residual = b.as_sql()
                check(f'insert {index} full SQL and exact original/residual parameters equal',
                      exact_parameters(original) == exact_parameters(residual)
                      and len(original) == 1 and original[0][0] == event['sql']
                      and base.encode(original[0][1]) == event['params'])
                check(f'insert {index} dynamic preparation counts retained',
                      ca['prepare_value'] == cb['prepare_value'] == len(columns)
                      and ca['pre_save_val'] == cb['pre_save_val'] == len(columns))
                for arm, counts in [('original', ca), ('residual', cb)]:
                    totals[arm].update(counts)
                results.append(dict(table=table, columns=columns, json_input_reconstructed_from_full_state=reconstructed,
                                    original=exact_parameters(original), residual=exact_parameters(residual),
                                    calls={'original': dict(ca), 'residual': dict(cb)}))
            check('actual pure dispatch work eliminated', totals['original']['field_as_sql'] == 335
                  and totals['residual']['field_as_sql'] == 45 and assembler.counts['residual_calls'] == 20)
            check('all four JSON fields/adapters retain original assembly', assembler.fallbacks == {'raw-or-custom-field': 4})
            save('observed-compilation.json', dict(statements=results, totals={k: dict(v) for k, v in totals.items()},
                                                 specialization=dict(assembler.counts), fallbacks=dict(assembler.fallbacks)))

            # Test values independently of observed business rows and widths.
            model = models['order_line']
            compiler = InsertQuery(model).get_compiler(using='default')
            rng = random.Random(20261107)
            general = LiteralInsertAssembler()
            generated = []
            widths = [1, 2, 5, 8, 21, 37]
            for i in range(96):
                width = widths[i % len(widths)]
                equal_a, equal_b = Decimal('123456.789'), Decimal('123456.789')
                pool = [None, bool(i % 2), rng.randrange(-(2**100), 2**100), rng.random(),
                        '未见数据 café %s \' ' + str(rng.randrange(10**9)), b'\x00\xff',
                        Decimal(rng.randrange(-10**8, 10**8)).scaleb(-3), equal_a, equal_b,
                        datetime.date(2026, 11, 7), datetime.datetime(2026, 11, 7, 1, 2, 3),
                        datetime.time(1, 2, 3), datetime.timedelta(days=-3, seconds=10), UUID(int=i)]
                row = [pool[j % len(pool)] for j in range(width)]
                if width >= 5:
                    row[:5] = [equal_a, equal_b, equal_a, None, '']
                fields = [CharField() for _ in row]
                expected = PgInsert.assemble_as_sql(compiler, fields, [row])
                got = general.assemble(compiler, fields, [row])
                check(f'generated {i} equality and every value identity', expected == got and
                      all(x is y for x, y in zip(row, got[1][0])))
                check(f'generated {i} fresh parameter containers', got[1][0] is not row and got[1] is not got[1][0])
                if width >= 5:
                    check(f'generated {i} equal distinct and shared values retained',
                          got[1][0][0] is not got[1][0][1] and got[1][0][0] is got[1][0][2])
                got[1][0].clear()
                again = general.assemble(compiler, fields, [row])
                check(f'generated {i} output mutation does not poison shape', again == expected)
                generated.append(dict(index=i, width=width, parameter_types=[type(v).__name__ for v in row]))
            check('cache contains only placeholder shapes, no data', all(
                v == (('%s',) * k,) for k, v in general.shapes.items()))
            save('generated-cases.json', dict(seed=20261107, cases=generated, counts=dict(general.counts)))

            # Negative controls exercise the original path and original errors.
            class PlaceholderField(CharField):
                def get_placeholder(self, value, compiler, connection):
                    return '%s::text'
            class PlaceholderFailure(CharField):
                def get_placeholder(self, value, compiler, connection):
                    raise ValueError('original placeholder failure')
            class CustomScalar:
                pass
            controls = [
                ('expression', [CharField()], [[Value('expression', output_field=CharField())]]),
                ('custom-placeholder', [PlaceholderField()], [['text']]),
                ('placeholder-error', [PlaceholderFailure()], [['text']]),
                ('custom-value', [CharField()], [[CustomScalar()]]),
                ('raw-default', [None], [['DEFAULT']]),
                ('empty', [CharField()], []),
                ('bulk-unnest', [IntegerField()], [[1], [2]]),
                ('row-width', [CharField(), CharField()], [['one']]),
            ]
            negatives = []
            def outcome(fn):
                try:
                    result = fn()
                    return ('value', result)
                except Exception as error:
                    return ('error', type(error).__qualname__, str(error))
            for label, fields, rows in controls:
                fallback = LiteralInsertAssembler()
                expected = outcome(lambda: PgInsert.assemble_as_sql(compiler, fields, rows))
                got = outcome(lambda: fallback.assemble(compiler, fields, rows))
                check(label + ' original output/error and guarded fallback',
                      expected == got and sum(fallback.fallbacks.values()) == 1 and not fallback.shapes)
                negatives.append(dict(case=label, outcome=base.encode(got), fallback=dict(fallback.fallbacks)))
            # A parameter hook with effects must run, not be inferred pure from
            # the fact that its eventual output happens to equal its input.
            hook_events = []
            custom_ops = copy.copy(compiler.connection.ops)
            def hook(placeholder, params):
                hook_events.append((placeholder, list(params)))
                return params
            custom_ops.modify_insert_params = hook
            with patch.object(compiler.connection, 'ops', custom_ops):
                fallback = LiteralInsertAssembler()
                expected = PgInsert.assemble_as_sql(compiler, [CharField()], [['hook']])
                got = fallback.assemble(compiler, [CharField()], [['hook']])
                check('custom parameter hook effects retained', expected == got and len(hook_events) == 2
                      and fallback.fallbacks == {'custom-parameter-hook': 1})
                negatives.append(dict(case='effectful-identity-hook', events=hook_events, fallback=dict(fallback.fallbacks)))
            save('negative-controls.json', negatives)
            check('no database connection opened', connection.connection is None)

        # Bound the opportunity with exclusive CPU; these totals include all
        # application INSERTs, so do not attribute them to the 26 target writes.
        costs = []
        for repeat in [0, 1]:
            functions = load(PROFILE / f'profiles/b{repeat}-original-profile/cpu-profile.json')['functions']
            chosen = [f for f in functions if f['name'].startswith('SQLInsertCompiler.assemble_as_sql')
                      or f['name'] in ['SQLInsertCompiler.field_as_sql', 'BaseDatabaseOperations.modify_insert_params']]
            total = sum(f['self_cpu_ms'] for f in functions)
            costs.append(dict(repeat=repeat, selected_self_cpu_ms=sum(f['self_cpu_ms'] for f in chosen),
                              total_self_cpu_ms=total, functions=[{k: f[k] for k in ['module', 'name', 'calls', 'self_cpu_ms']} for f in chosen]))
        save('cost-and-decision.json', dict(
            profile_costs=costs,
            conclusion='Pure assembly specialization preserves tested compiler outputs and removes actual intermediate calls; cost coverage is too small to prioritize integration.',
            next='Retain this feasibility witness; inspect the larger SELECT Query/build_filter/compile chain before selecting another residual.',
            do_not='Do not install, rerun DB benchmarks, claim application speedup, or delete unobserved modules.',
            limitations=['Offline compiler differential only, no new end-to-end runtime evidence',
                        'This leaves QuerySet construction, SQL outer assembly, preparation and all DB calls unchanged',
                        'Measured old self CPU is perturbed and not a predicted wall-clock saving',
                        'No claim of general source-free decompilation or automatic synthesis',
                        'Same DP core retained, but this host specialization is not represented/admitted in its DB-call objective']))
        report.update(status='passed', observed_statements=26, generated_cases=96, negative_controls=len(negatives))
        check('all experiment sources unchanged', all(sha(Path(p)) == h for p, h in report['inputs'].items()))
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        save('run.json', report)
        save('manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'], output=str(out), checks=len(report['checks'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
