#!/usr/bin/env python3
"""Bounded falsification of albumtotal scalarization, not an admitted backend.

Retain the exact first SQL and all fixed rows: this experiment isolates removal
of the flex read and intermediate Item, without introducing LIMIT/projection.
"""
import ast
from contextlib import ExitStack
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'work/beets/source-e978874d'
BASE = ROOT / 'evidence/beets-feasibility/20261108-a03fafe1c20f'
sys.path.insert(0, str(ROOT / 'scripts'))
from test_beets_feasibility import snapshot, sha

CASES = [
    'default', 'reverse', 'ties', 'null', 'numeric-text', 'invalid-number',
    'empty', 'multidisc', 'flex-sort', 'converter-effect', 'converter-error',
    'getter-value', 'getter-error', 'constructor-escape', 'constructor-error',
    'deny-flex', 'invalid-flex-text', 'deny-fixed', 'concurrent-fixed',
    'concurrent-flex-getter',
]
ARMS = ['original', 'drop-flex-and-model', 'retain-flex-drop-model']
EXPECTED_DROP = {'getter-value', 'getter-error', 'constructor-escape', 'constructor-error',
                 'deny-flex', 'invalid-flex-text', 'concurrent-flex-getter'}
EXPECTED_KEEP = EXPECTED_DROP - {'deny-flex', 'invalid-flex-text'}


def main():
    out = ROOT / 'evidence/beets-scalar-contract' / ('20261108-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    def save(name, value):
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    save('protocol.json', dict(cases=CASES, arms=ARMS, timings=False,
        expected_drop_counterexamples=sorted(EXPECTED_DROP),
        expected_keep_counterexamples=sorted(EXPECTED_KEEP),
        seed=os.environ.get('PYTHONHASHSEED'), full_export_items=100,
        scope='Manual source-derived mechanism; no optimizer integration or deployment',
        candidate='Exact original fixed SQL and rows; original scalar converter; remove Item; optionally remove flex query',
        fallback='Original method for multidisc branch or slow sort',
        decision='Any observed effect/error mismatch rejects unrestricted replacement; no extra performance runs',
        observables='Value and type, exception class/message, callback events, escaped identities, full schema and rows'))
    shutil.copy2(__file__, out / 'probe.py')
    (out / 'config').mkdir()
    (out / 'config/config.yaml').write_text('plugins: []\n')
    os.environ['BEETSDIR'] = str(out / 'config')
    sys.path.insert(0, str(SOURCE))
    import beets
    from beets import plugins
    from beets.library import Album, Item, Library
    from beets.dbcore import db as core, query
    from beetsplug.export import ExportPlugin

    beets.config['plugins'] = []
    original = Album._albumtotal
    original_query = core.Transaction.query
    original_init = core.Model.__init__
    original_getters = plugins.item_field_getters
    default_sort = beets.config['sort_item'].get()
    watched = [SOURCE / p for p in ['beets/dbcore/db.py', 'beets/library/models.py',
               'beets/library/library.py', 'beets/dbcore/types.py', 'beetsplug/export.py']]
    watched += [ROOT / 'scripts' / p for p in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']]
    initial_hashes = {str(p.relative_to(ROOT)): sha(p) for p in watched}
    save('inputs.json', initial_hashes)
    check('pinned upstream sources', all(sha(SOURCE / p) == h for p, h in
          json.loads((BASE / 'source-manifest.json').read_text()).items()))

    # Copy the upstream SQL construction AST, retaining its original text/order.
    # Only substitute flex fetch and terminal Results construction. This is
    # source specialization for a falsification test, not decompilation proof.
    source = textwrap.dedent(inspect.getsource(core.Database._get_results))
    readers = {}
    for arm in ARMS[1:]:
        tree = ast.parse(source)
        fn = tree.body[0]
        fn.name = 'read_fixed_rows'
        fn.returns = None
        for arg in fn.args.args:
            arg.annotation = None
        replacements = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'flex_rows' for t in node.targets):
                if arm == 'drop-flex-and-model':
                    node.value = ast.List(elts=[], ctx=ast.Load())
                replacements += 1
        check(arm + ' one flex assignment', replacements == 1)
        check(arm + ' original terminal Results', isinstance(fn.body[-1], ast.Return) and
              isinstance(fn.body[-1].value, ast.Call) and fn.body[-1].value.func.id == 'Results')
        fn.body[-1] = ast.Return(value=ast.Name(id='rows', ctx=ast.Load()))
        ast.fix_missing_locations(tree)
        generated = ast.unparse(tree) + '\n'
        (out / (arm + '.py')).write_text(generated)
        namespace = dict(core.__dict__)
        exec(compile(generated, str(out / (arm + '.py')), 'exec'), namespace)
        readers[arm] = namespace['read_fixed_rows']

    fallbacks = []
    def candidate(album, arm):
        if not (album.disctotal == 1 or not beets.config['per_disc_numbering']):
            fallbacks.append('multidisc')
            return original(album)
        if album._db is None:
            raise AttributeError(f'{type(album).__name__} has no database')
        sort = album._db.get_default_item_sort()
        if sort.is_slow():
            fallbacks.append('slow-sort')
            return original(album)
        rows = readers[arm](album._db, Item, query.MatchQuery('album_id', album.id), sort)
        return Item._convert('tracktotal', rows[0]['tracktotal'])

    def clone(path):
        with sqlite3.connect(BASE / '100/library.db') as src, sqlite3.connect(path) as dst:
            src.backup(dst)

    results = []
    try:
        for case in CASES:
            arms = []
            for arm in ARMS:
                folder = out / case / arm
                folder.mkdir(parents=True)
                path = folder / 'library.db'
                clone(path)
                lib = Library(path, directory=BASE / '100/music')
                beets.config['sort_item'] = default_sort
                beets.config['per_disc_numbering'] = False
                events, escaped, calls = [], [], []
                fallbacks.clear()
                with sqlite3.connect(path) as sql:
                    sql.execute('UPDATE albums SET disctotal=1 WHERE id=1')
                    sql.execute('UPDATE items SET tracktotal=17 WHERE album_id=1')
                    if case == 'reverse':
                        beets.config['sort_item'] = ['track-']
                        sql.execute('UPDATE items SET tracktotal=100+track WHERE album_id=1')
                    if case == 'ties':
                        beets.config['sort_item'] = ['title+']
                        sql.execute("UPDATE items SET title='same', tracktotal=100+id WHERE album_id=1")
                    if case in ['null', 'numeric-text', 'invalid-number']:
                        value = {'null': None, 'numeric-text': '17.6', 'invalid-number': 'not-a-number'}[case]
                        sql.execute('UPDATE items SET tracktotal=? WHERE album_id=1', (value,))
                    if case == 'multidisc':
                        beets.config['per_disc_numbering'] = True
                        sql.execute('UPDATE albums SET disctotal=2 WHERE id=1')
                        sql.execute('UPDATE items SET disc=CASE WHEN track<=5 THEN 1 ELSE 2 END, tracktotal=CASE WHEN track<=5 THEN 4 ELSE 6 END WHERE album_id=1')
                    if case == 'flex-sort':
                        beets.config['sort_item'] = ['rating_hint-']
                        sql.execute('UPDATE items SET tracktotal=100+id WHERE album_id=1')
                    if case == 'invalid-flex-text':
                        sql.execute("UPDATE item_attributes SET value=CAST(X'80' AS TEXT) WHERE key='probe_tag' AND entity_id IN (SELECT id FROM items WHERE album_id=1)")
                album = lib.get_album(1)
                if case == 'empty':
                    album = Album(lib, album='Empty contract case', disctotal=1, added=1700000000.0)
                    with patch.object(core.time, 'time', return_value=1700000000.0):
                        album.add(lib)
                # hex-encoding SQLite TEXT permits retaining deliberately bad UTF-8.
                def full_state():
                    with sqlite3.connect(path) as conn:
                        conn.text_factory = bytes
                        schema = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
                        tables = {}
                        for (raw,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                            name = raw.decode()
                            rows = conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                            tables[name] = sorted([repr(row) for row in rows])
                        return dict(schema=[repr(row) for row in schema], tables=tables)
                before = full_state()
                save(f'{case}/{arm}/before.json', before)
                def run_query(tx, statement, params=()):
                    calls.append(dict(sql=statement, params=list(params)))
                    values = original_query(tx, statement, params)
                    if case.startswith('concurrent-') and len(calls) == 1:
                        with sqlite3.connect(path) as other:
                            other.execute('UPDATE items SET tracktotal=29 WHERE album_id=1')
                            other.execute("UPDATE item_attributes SET value='new' WHERE key='probe_tag' AND entity_id IN (SELECT id FROM items WHERE album_id=1)")
                        events.append('external-commit-after-fixed-read')
                    return values
                def init(obj, *args, **kwargs):
                    original_init(obj, *args, **kwargs)
                    if isinstance(obj, Item):
                        events.append(['constructor', obj.id])
                        if case == 'constructor-error':
                            raise RuntimeError('constructor failure')
                        escaped.append(obj)
                def getter(obj):
                    events.append(['getter', obj.id])
                    if case == 'getter-error':
                        raise RuntimeError('getter failure')
                    return 29 if obj['probe_tag'] == 'new' else 23
                def authorize(action, table, column, db, trigger):
                    denied = 'item_attributes' if case == 'deny-flex' else 'items'
                    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == denied else sqlite3.SQLITE_OK
                typ = Item._type('tracktotal')
                convert = typ.from_sql
                def conversion(value):
                    events.append(['convert', value])
                    if case == 'converter-error':
                        raise RuntimeError('conversion failure')
                    return convert(value)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(core.Transaction, 'query', run_query))
                    if case.startswith('constructor-'):
                        stack.enter_context(patch.object(core.Model, '__init__', init))
                    if case.startswith('getter-') or case == 'concurrent-flex-getter':
                        stack.enter_context(patch.object(plugins, 'item_field_getters', lambda: {**original_getters(), 'tracktotal': getter}))
                    if case.startswith('converter-'):
                        stack.enter_context(patch.object(typ, 'from_sql', conversion))
                    if case in ['deny-fixed', 'deny-flex']:
                        lib._connection().set_authorizer(authorize)
                    try:
                        value = original(album) if arm == 'original' else candidate(album, arm)
                        outcome = dict(value=value, type=type(value).__name__)
                    except Exception as exc:
                        outcome = dict(error=type(exc).__name__, message=str(exc))
                    finally:
                        lib._connection().set_authorizer(None)
                after = full_state()
                save(f'{case}/{arm}/after.json', after)
                check(f'{case}/{arm} complete data retained', before == after or case.startswith('concurrent-'))
                record = dict(arm=arm, outcome=outcome, events=events, escaped_objects=len(escaped),
                              queries=calls, fallback=list(fallbacks))
                save(f'{case}/{arm}/result.json', record)
                arms.append((record, after))
                lib._close()
            base, baseline_state = arms[0]
            row = dict(case=case, arms=[x[0] for x in arms], equal={})
            for record, state in arms[1:]:
                check(case + '/' + record['arm'] + ' final full state equal', state == baseline_state)
                equal = all(record[k] == base[k] for k in ['outcome', 'events', 'escaped_objects'])
                row['equal'][record['arm']] = equal
                check(case + '/' + record['arm'] + ' first SQL identical', record['queries'][0] == base['queries'][0])
            results.append(row)
            save('cases.json', results)
            print(case, row['equal'], flush=True)

        expected_drop = EXPECTED_DROP
        expected_keep = EXPECTED_KEEP
        for arm, expected in zip(ARMS[1:], [expected_drop, expected_keep]):
            check(arm + ' exact predeclared counterexamples', {r['case'] for r in results if not r['equal'][arm]} == expected)

        # A complete default export is a positive control, never a universal proof.
        exports = []
        beets.config['sort_item'] = default_sort
        beets.config['per_disc_numbering'] = False
        plugin = ExportPlugin()
        for arm in ['original', 'drop-flex-and-model']:
            folder = out / 'export' / arm
            folder.mkdir(parents=True)
            path = folder / 'library.db'
            clone(path)
            lib = Library(path, directory=BASE / '100/music')
            with sqlite3.connect(path) as conn:
                conn.execute('UPDATE items SET tracktotal=17 WHERE album_id IS NOT NULL')
            before = snapshot(path)
            save(f'export/{arm}/before.json', before)
            queries, models = [], []
            make_model = core.Results._make_model
            def count_query(tx, sql, params=()):
                queries.append(dict(sql=sql, params=list(params)))
                return original_query(tx, sql, params)
            def count_model(obj, *args, **kwargs):
                models.append(obj.model_class.__name__)
                return make_model(obj, *args, **kwargs)
            with ExitStack() as stack:
                stack.enter_context(patch.object(core.Transaction, 'query', count_query))
                stack.enter_context(patch.object(core.Results, '_make_model', count_model))
                if arm != 'original':
                    stack.enter_context(patch.object(Album, '_albumtotal', lambda album: candidate(album, arm)))
                plugin.run(lib, SimpleNamespace(output=str(folder / 'output.json'), append=False,
                    format='json', library=True, album=False, included_keys=[], limit=None), ['id+'])
            after = snapshot(path)
            check(arm + ' export full database unchanged', before == after)
            save(f'export/{arm}/after.json', after)
            save(f'export/{arm}/queries.json', queries)
            exports.append(dict(arm=arm, queries=len(queries), models=len(models), output_sha256=sha(folder / 'output.json')))
            lib._close()
        check('complete export bytes equal', exports[0]['output_sha256'] == exports[1]['output_sha256'])
        check('complete export expected physical queries', [r['queries'] for r in exports] == [362, 272])
        check('complete export expected model counts', [r['models'] for r in exports] == [280, 190])
        check('source and shared algorithm unchanged', all(sha(ROOT / p) == h for p, h in initial_hashes.items()))
        save('report.json', dict(status='passed', checks=checks, cases=len(results), local_executions=len(results)*3,
            exports=exports, unrestricted_candidate='rejected', backend_admitted=False, speedup_measured=False,
            drop_counterexamples=sorted(expected_drop), retained_read_counterexamples=sorted(expected_keep),
            conclusion='Normal-path scalarization works; removing flex read loses real read errors; removing Item loses dynamic effects. Preserve original path; no performance or shared-DP admission.',
            limitation='Manual source specialization and bounded counterexamples, not proof for all configurations or independent decompilation'))
    except BaseException as exc:
        save('failure.json', dict(type=type(exc).__name__, message=str(exc), checks=checks))
        raise
    finally:
        save('manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json'})
        print(out, flush=True)


if __name__ == '__main__':
    main()
