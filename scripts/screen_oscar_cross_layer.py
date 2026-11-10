#!/usr/bin/env python3
"""Bounded evidence screening and original-Python effect counterexamples.

No SQL rewriting, database execution, timing, or new placement algorithm.
"""
import copy
from decimal import Decimal
import dis
import hashlib
import json
import marshal
import os
from pathlib import Path
import shutil
import sys
import types
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evidence/oscar-baseline/20261106-ed9f704d86d6'
PROFILE = ROOT / 'evidence/oscar-module-profile/20261107-a70b2db5616e'
FIRST = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce'
SOURCE = ROOT / 'work/oscar/source-a5ed3c9c'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p):
    return json.loads(p.read_text())


def main():
    out = ROOT / 'evidence/oscar-cross-layer-screen' / ('20261107-' + uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status='running', checks=[], inputs={}, database_executions=0, optimizer_changed=False)
    def save(n, value):
        (out / n).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    def check(n, ok):
        assert ok, n
        report['checks'].append(n)
    def retain(folder, name):
        p = folder / name
        assert sha(p) == load(folder / 'manifest.json')[name], name
        report['inputs'][str(p)] = sha(p)
        return p
    try:
        shutil.copy2(__file__, out / 'screen_oscar_cross_layer.py')
        save('protocol.json', dict(scope='screen three original cross-layer chains; select one conditional research question',
             tests='original num_items/from_db on complete in-memory rows; bag/empty/integer and post_init controls',
             stop='no database run, candidate implementation, new performance samples or new optimizer'))
        for f in ['source-manifest.json', 'a/after-full-state.json', 'a/executed-code.json', 'a/receivers.json']:
            retain(BASE, f)
        for f, h in load(BASE / 'source-manifest.json').items():
            assert sha(SOURCE / f) == h, f
        witnesses = load(retain(PROFILE, 'profiles/bytecode-witness.json'))
        for w in witnesses:
            assert sha(Path(w['source'])) == w['source_sha256'], w['module']
        for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
            check('unchanged original optimizer ' + name, sha(ROOT / 'scripts' / name) == sha(retain(FIRST, 'inputs/scripts/' + name)))
        sys.path[:0] = [str(ROOT / 'prototype/oscar'), str(ROOT / 'scripts'), str(SOURCE / 'src'), str(SOURCE / 'sandbox')]
        sys.argv = [sys.argv[0], 'screen', str(out)]
        import baseline_probe as base
        from placement_frontend import select_complete_contract
        bytecode = []
        def walk(c):
            yield c
            for v in c.co_consts:
                if isinstance(v, types.CodeType):
                    yield from walk(v)
        targets = {
            'oscar.apps.analytics.receivers': ['_record_user_order', '_record_products_in_order', '_update_counter'],
            'oscar.apps.order.abstract_models': ['AbstractOrder.num_lines', 'AbstractOrder.num_items'],
            'oscar.apps.customer.alerts.utils': ['AlertsDispatcher.send_product_alert_email_for_user'],
            'django.db.models.base': ['Model.from_db', 'Model.__init__'],
        }
        recovered = {}
        for module, names in targets.items():
            p = retain(PROFILE, 'profiles/observed-pyc/' + module + '.pyc')
            codes = {c.co_qualname: c for c in walk(marshal.loads(p.read_bytes()[16:]))}
            for name in names:
                c = codes[name]; recovered[name] = c
                bytecode.append(dict(module=module, name=name, hash=base.code_hash(c),
                                     instructions=[dict(offset=i.offset, op=i.opname, arg=i.argrepr) for i in dis.get_instructions(c)],
                                     exception_table_hex=c.co_exceptiontable.hex()))
        save('source-bytecode.json', bytecode)

        codes = load(BASE / 'a/executed-code.json')
        rows = []; user_prefixes = []; product_saved = 0
        for number in ['RDB-ONE', 'RDB-THREE', 'RDB-SIX', 'RDB-DIGITAL']:
            events = load(retain(BASE, 'a/' + number + '-trace.json'))
            sqls = [e for e in events if e['kind'] == 'execute']
            def in_method(e, name):
                return any(codes[f['code']]['name'] == name for f in e['stack'])
            user = [e for e in sqls if in_method(e, '_record_user_order')]
            update_index = next(i for i,e in enumerate(user) if e['sql'].startswith('UPDATE "analytics_userrecord"'))
            prefix = user[:update_index+1]
            check(number + ' original count/read/update prefix', len(prefix) == 3 and prefix[0]['sql'].startswith('SELECT COUNT(*)')
                  and prefix[1]['sql'].startswith('SELECT "order_line"') and prefix[2]['sql'].startswith('UPDATE "analytics_userrecord"'))
            check(number + ' unmodified typed parameter flow', prefix[0]['params'] == prefix[1]['params']
                  and prefix[2]['params'][1] == prefix[1]['rowcount'])
            user_prefixes.append(dict(order=number, events=prefix))
            products = [e for e in sqls if 'analytics_productrecord' in e['sql'] and in_method(e, '_update_counter')]
            updates = [e for e in products if e['sql'].startswith('UPDATE')]
            misses = sum(e['rowcount'] == 0 for e in updates)
            # A hypothetical ordered prefix stops immediately at the first miss
            # so original host CREATE/hooks run before later UPDATEs. Never UPSERT.
            prefix_calls = misses + int(bool(updates) and updates[-1]['rowcount'] != 0)
            saved = len(updates) - prefix_calls
            product_saved += saved
            alerts = [e for e in sqls if in_method(e, 'AlertsDispatcher.send_product_alert_email_for_user')]
            alert_counts = [e for e in alerts if e['sql'].startswith('SELECT COUNT(*)')]
            alert_reads = [e for e in alerts if e['sql'].startswith('SELECT "customer_productalert"')]
            check(number + ' observed alert count/read pairing only', len(alert_counts) == len(alert_reads))
            columns = prefix[1]['sql'].split(' FROM ', 1)[0].count(',') + 1
            rows.append(dict(order=number, user_calls=len(user), prefix_calls=3,
                             prefix_line_rows=prefix[1]['rowcount'], prefix_line_columns=columns,
                             prefix_update_affected=prefix[2]['rowcount'], continuation_calls=len(user)-3,
                             product_updates=len(updates), product_misses=misses,
                             ordered_product_prefix_calls=prefix_calls, product_calls_saved_if_verified=saved,
                             alert_calls=len(alerts), alert_pairs=len(alert_counts)))
        check('user stats source cost 18, prefix 12 and retained continuation six',
              sum(r['user_calls'] for r in rows) == 18 and sum(r['continuation_calls'] for r in rows) == 6)
        check('prefix raw payload eleven full 22-column rows', sum(r['prefix_line_rows'] for r in rows) == 11
              and {r['prefix_line_columns'] for r in rows} == {22})
        check('missing-counter barriers limit batching to two saved calls', product_saved == 2)
        screen = dict(observed_orders=rows, candidates=[
            dict(name='ordered count/materialize-sum/user-update', original_calls=18, hypothetical_calls=10,
                 saved_calls=8, saved_fraction_of_187=8/187, removable_prefix_rows_if_proven=11,
                 transferred_column_values_if_proven=242,
                 decision='prioritize a bounded semantic contract, not an optimization admission',
                 barriers=['read committed count/read/update snapshots', 'model initialization and converters',
                           'Python integer / SQL arithmetic and original parameter errors', 'original all-column SELECT privileges',
                           'UPDATE miss must run original re-read and CREATE/signals; no summary reuse']),
            dict(name='ordered product-counter update prefixes stopping at miss', original_update_calls=11,
                 hypothetical_update_calls=9, saved_calls=2,
                 decision='defer: small observed benefit while CREATE/signals/retry barriers must remain'),
            dict(name='product-alert ordered count/full-row packet', original_calls=30, hypothetical_calls=20,
                 saved_calls=10, decision='defer: modest call reduction and not a reason to delete empty-fixture alert capability',
                 barriers=['count and row scan may see different snapshots', 'nullable stock short-circuit',
                           'multiple-stock aggregate branch', 'selector/e-mail/user notification effects', 'typed full-row ABI'])],
            limits=['hypothetical call budgets only; no implementation or speedup',
                    'observed row counts rank this workload, never bound legal data or justify deletion',
                    'raw scalar counts are not byte or memory estimates',
                    'no combined-candidate/global plan; conditional branches outside the selected prefix remain original'])
        save('screening.json', screen)
        save('prefix-source-witnesses.json', user_prefixes)

        prefix = user_prefixes[0]['events']
        obligations = ['original_READ_COMMITTED_statement_order', 'original_all_projected_column_permissions',
                       'prove_model_materialization_has_no_effect_or_retain_it', 'Python_bag_sum_empty_zero_and_integer_domain',
                       'preserve_original_argument_preparation_and_failure_order', 'UPDATE_rowcount_locks_and_errors',
                       'on_miss_resume_original_reread_CREATE_and_signals']
        definitions = [dict(num_lines='int64'), dict(num_items='python_int'), dict(affected='int64')]
        uses = [dict(order_id='int64'), dict(order_id='int64'),
                dict(num_lines='int64', num_items='python_int', user_id='int64', total='decimal', placed='timestamp_tz')]
        ids = ['count_lines', 'read_materialize_sum', 'update_user_record']
        nodes = []
        for i, e in enumerate(prefix):
            nodes.append(dict(id=ids[i], queries=[e['sql']], uses=uses[i], defines=definitions[i],
                body_known=True, transaction='original_outer_read_committed', java_calls=dict(base=1,terms={}),
                effects=dict(reads=['order_line'] if i<2 else ['analytics_userrecord'],
                             writes=[] if i<2 else ['analytics_userrecord'], locks=[] if i<2 else ['analytics_userrecord']),
                database=None, requires=obligations, source_stack=e['stack'],
                retention_reason='No verified source-bound relational lowering and result/exception ABI; profiling alone is not admission'))
        nodes.append(dict(id='original_host_continuation_marker', queries=[], uses=dict(affected='int64'), defines={},
                          body_known=True, transaction='original_outer_read_committed', java_calls=dict(base=0,terms={}),
                          database=None, host_retention=dict(required=True,reason='marker only; actual fallback queries excluded from this prefix contract'),
                          retention_reason='Original affected-row branch and re-read/CREATE continuation remain outside this scope'))
        contract = dict(scope='one count/read-sum/UPDATE prefix; host marker exposes live affected result, does not price fallback execution',
                        external_inputs=dict(order_id='int64',user_id='int64',total='decimal',placed='timestamp_tz'),
                        parameters={},wire_types={},nodes=nodes,
                        edges=[dict(before='count_lines',after='read_materialize_sum',reason='source order and independent read snapshots'),
                               dict(before='count_lines',after='update_user_record',reason='count value'),
                               dict(before='read_materialize_sum',after='update_user_record',reason='sum value and failure order'),
                               dict(before='update_user_record',after='original_host_continuation_marker',reason='affected-row branch')])
        selection = select_complete_contract(contract)
        check('same DP conservatively retains all three calls', selection['selection']['selected_calls'] == dict(base=3,terms={})
              and not selection['selection']['database_regions'])
        save('prefix-contract.json', contract); save('prefix-selection.json', selection)

        os.environ.update(DJANGO_SETTINGS_MODULE='settings', DATABASE_ENGINE='django.db.backends.postgresql',
                          DATABASE_NAME='offline', DATABASE_HOST='127.0.0.1', DATABASE_PORT='1')
        from django.db.backends.postgresql.base import DatabaseWrapper
        with patch.object(DatabaseWrapper,'get_new_connection',side_effect=AssertionError('No database access in semantic examples')):
            import django
            django.setup()
            from django.db import connection
            from django.db.models.signals import post_init, pre_init
            from oscar.core.loading import get_model
            Order = get_model('order','Order'); Line = get_model('order','Line')
            check('executed original num_items and from_db match retained bytecode',
                  base.code_hash(Order.num_items.fget.__code__) == base.code_hash(recovered['AbstractOrder.num_items'])
                  and base.code_hash(Line.from_db.__func__.__code__) == base.code_hash(recovered['Model.from_db']))
            check('original materialization listeners absent in this frozen configuration', not post_init.has_listeners(Line) and not pre_init.has_listeners(Line))
            full = json.loads((BASE/'a/after-full-state.json').read_text(),parse_float=Decimal)['tables']['order_line']
            original_rows = sorted([r for r in full if r['order_id']==3],key=lambda r:r['id'])
            fields = [f.attname for f in Line._meta.concrete_fields]
            created = []
            class Rows:
                def __init__(self, rows):self.rows=rows
                def all(self):
                    materialized=[Line.from_db('default',fields,[r[f] for f in fields]) for r in self.rows]
                    created.extend(materialized)
                    return materialized
            def original(rows):
                return Order.num_items.fget(types.SimpleNamespace(lines=Rows(rows)))
            bag = copy.deepcopy(original_rows[:2])
            for row in bag:row.update(product_id=1,quantity=2)
            wide = copy.deepcopy(bag)
            for row in wide:row['quantity']=2**31-1
            zero = copy.deepcopy(bag)
            for row in zero:row['quantity']=0
            examples=[]
            for label, data in [('complete-six-line-rowset',original_rows),('empty',[]),('repeated-product-bag',bag),('wide-sum',wide),('zero-quantities',zero)]:
                created.clear(); actual=original(data); algebra=sum(row['quantity'] for row in data)
                check(label+' original Python sum equals bag algebra example',actual==algebra and len(created)==len(data))
                examples.append(dict(case=label,rows=len(data),original_sum=actual,bag_algebra_sum=algebra))
            check('bag is not distinct values or products',original(bag)==4 and sum({r['quantity'] for r in bag})==2)
            check('Python intermediate sum exceeds int32 without premature failure',original(wide)==4294967294)
            def mutate(sender, instance, **kwargs):instance.quantity += 7
            post_init.connect(mutate,sender=Line,weak=False)
            try:
                check('effectful materialization is detectable',post_init.has_listeners(Line))
                actual=original(bag); algebra=sum(r['quantity'] for r in bag)
                check('raw SUM is wrong with an initialization effect',actual==18 and algebra==4)
                examples.append(dict(case='post-init-mutates-quantity',original_sum=actual,raw_bag_sum=algebra,equivalent=False))
            finally:post_init.disconnect(mutate,sender=Line)
            def fail(sender, instance, **kwargs):raise RuntimeError('original Line initialization failure')
            post_init.connect(fail,sender=Line,weak=False)
            try:
                try:original(bag)
                except RuntimeError as e:error=str(e)
                else:raise AssertionError('Original materialization failure disappeared')
                check('materialization failure cannot become a scalar result',error=='original Line initialization failure')
                examples.append(dict(case='post-init-fails',original_error=error,raw_bag_sum=4,equivalent=False))
            finally:post_init.disconnect(fail,sender=Line)
            check('listeners restored and no database connection opened',not post_init.has_listeners(Line) and connection.connection is None)
            save('materialization-boundaries.json',dict(examples=examples,
                scope='Original Django from_db and original num_items, supplied complete in-memory rows; algebra oracle is Python, NOT a PostgreSQL candidate',
                outstanding=['actual SQL aggregate/error semantics', 'READ COMMITTED interleavings', 'full projection ACL and RLS',
                             'converters, custom properties/managers and UPDATE arithmetic', 'unmodified missing-record continuation']))
        save('decision.json',dict(status='bounded candidate formalized, not implemented or admitted',
            candidate='ordered relationalization of count / nonescaping row-materialization sum / UPDATE',
            observed_budget='12 prefix executions -> hypothetical 4; six fallback calls retained; total user chain 18 -> 10; saves only 8/187',
            scientific_question='Can a materialization subtree be eliminated under a source/configuration-bound effect contract and fused with its database consumer while preserving statement boundaries?',
            next='first verify typed aggregate, separate read snapshots and unchanged zero-row continuation on a small real DB contract; stop if any boundary remains unknown',
            excluded=['no data deletion', 'no UPSERT substitution', 'no count=len(rows) assumption', 'no single-snapshot collapse',
                      'no reuse of pre-UPDATE summaries in missing-record branch', 'no performance or production claim']))
        check('all retained evidence unchanged',all(sha(Path(p))==h for p,h in report['inputs'].items()))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))))


if __name__=='__main__':main()
