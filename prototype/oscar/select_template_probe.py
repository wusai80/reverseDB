"""Bounded compiler, real-read and fixed-order differentials for SELECT staging."""
from collections import Counter
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import baseline_probe as base

OUT = Path(sys.argv[2])
STATE = dict(active=False, placements=0)


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS'] = dict(cursor_factory=base.TraceCursor, connection_factory=base.TraceConnection)
    django.setup()
    from django.db import connection, transaction
    from django.db.backends.postgresql.base import DatabaseWrapper
    from django.db.models import Model, signals
    from django.db.models.fields import IntegerField
    from django.db.models.lookups import IntegerFieldExact, IntegerFieldOverflow
    from django.db.models.sql.compiler import SQLCompiler
    from django.db.models.sql.query import Query
    from oscar.core.loading import get_model
    from oscar.apps.order.utils import OrderCreator
    from select_template import installed, SelectTemplates, ORIGINAL
    from select_chain_probe import abi
    checks = []
    def check(name, ok):
        assert ok, name
        checks.append(name)
    watched = {f.__code__: name for name, f in [('get_select', SQLCompiler.get_select),
        ('compile', SQLCompiler.compile), ('build_filter', Query.build_filter),
        ('model_init', Model.__init__), ('from_db', Model.from_db.__func__),
        ('integer_prepare', IntegerField.get_prep_value), ('range_check', IntegerFieldOverflow.process_rhs),
        ('get_converters', SQLCompiler.get_converters)]}
    counts = Counter()
    old_profile = base.profile
    def profile(frame, event, arg):
        old_profile(frame, event, arg)
        if event == 'call' and frame.f_code in watched:
            counts[watched[frame.f_code]] += 1
    @contextmanager
    def counted():
        assert sys.getprofile() is None
        sys.setprofile(profile)
        try:
            yield
        finally:
            sys.setprofile(None)

    if sys.argv[1] == 'offline':
        records = json.loads(Path(os.environ['RDB_SELECT_RECORDS']).read_text())
        selected = [r for r in records if r['kind'] == 'select_compile' and r['facts']['integer_pk_point_candidate']]
        def compiler_for(record, value, consumer='model'):
            model = get_model(*record['facts']['model'].split('.'))
            fields = [s['expression']['field'] for s in record['facts']['result_abi']['select']]
            qs = model._base_manager.filter(pk=value).order_by()
            if consumer == 'values':
                qs = qs.values(*fields)
            elif consumer == 'tuples':
                qs = qs.values_list(*fields)
            elif len(fields) != len(model._meta.concrete_fields):
                qs = qs.only(*fields)
            qs.query.set_limits(high=21)
            return qs.query.get_compiler(using='default')
        def evaluate(record, value, consumer='model'):
            try:
                compiler = compiler_for(record, value, consumer)
                sql, params = compiler.as_sql()
                return dict(sql=sql, params=base.encode(params), abi=abi(compiler),
                            execution_flags=dict(has_extra_select=compiler.has_extra_select,
                                                 having=compiler.having is not None, qualify=compiler.qualify is not None),
                            output_types=[dict(target=s[0].target.model._meta.label + '.' + s[0].target.name,
                                               output=s[0].output_field.model._meta.label + '.' + s[0].output_field.name,
                                               type=type(s[0].output_field).__name__) for s in compiler.select])
            except Exception as error:
                return dict(error=type(error).__name__, message=str(error))
        with patch.object(DatabaseWrapper, 'get_new_connection', side_effect=AssertionError('Offline test attempted DB access')):
            arms, summaries = {}, {}
            for arm in ['original', 'candidate']:
                counts.clear()
                result = []
                with installed() if arm == 'candidate' else nullcontext() as templates:
                    with counted():
                        for r in selected:
                            result.append(evaluate(r, r['params'][0]))
                    if templates:
                        summaries[arm] = templates.summary()
                arms[arm] = dict(results=result, counts=dict(counts))
            base.save('offline-compilation.json', dict(arms=arms, specialization=summaries))
            check('46 observed SQL/parameter/result-ABI compilations identical', arms['original']['results'] == arms['candidate']['results'] and len(selected) == 46)
            check('original reconstructed compilers match executed evidence', all(
                {k:v for k,v in x.items() if k not in ['output_types', 'execution_flags']} == dict(sql=r['sql'], params=r['params'], abi=r['facts']['result_abi'])
                for x, r in zip(arms['original']['results'], selected)))
            check('get_select reduced from 46 to six cold compilations', arms['original']['counts']['get_select'] == 46 and arms['candidate']['counts']['get_select'] == 6)
            check('all original filter and integer checks retained', all(arms['original']['counts'][k] == arms['candidate']['counts'][k]
                  for k in ['build_filter', 'integer_prepare', 'range_check']))

            shapes = {}
            for r in selected:
                shapes.setdefault(r['sql'], r)
            boundaries = []
            for r in shapes.values():
                model = get_model(*r['facts']['model'].split('.'))
                minimum, maximum = connection.ops.integer_field_range(model._meta.pk.get_internal_type())
                for value in [314159, 0, -1, maximum, maximum+1, minimum-1, None, 'not-an-integer']:
                    original = evaluate(r, value)
                    with installed() as templates:
                        evaluate(r, 1)  # A real original cold compilation before testing a hit.
                        candidate = evaluate(r, value)
                    check(r['facts']['model'] + ' boundary ' + str(value), original == candidate)
                    boundaries.append(dict(model=r['facts']['model'], input=value, original=original, candidate=candidate, stats=templates.summary()))
            base.save('offline-boundaries.json', boundaries)

            # Mutable compiler outputs must not alias the immutable template.
            r = selected[0]
            with installed() as templates:
                a = compiler_for(r, 1); a.as_sql()
                b = compiler_for(r, 2); expected = b.as_sql(); expected_abi = abi(b)
                b.select[0][1][1].append('poison')
                b.klass_info['select_fields'].clear()
                b.select.clear()
                c = compiler_for(r, 2)
                check('consumer mutation cannot poison subsequent template', c.as_sql() == expected and abi(c) == expected_abi)
                check('template stores no query, RHS or result/model instances', all(
                    set(t.__dict__) == {'sql', 'where_sql', 'columns', 'model', 'select_fields'}
                    for t in templates.templates.values()))
            controls = []
            for consumer in ['values', 'tuples']:
                original = evaluate(r, 1, consumer)
                with installed() as templates:
                    evaluate(r, 1)
                    candidate = evaluate(r, 1, consumer)
                check(consumer + ' keeps original compiler path and ABI', original == candidate and sum(templates.fallbacks.values()) == 1)
                controls.append(dict(consumer=consumer, stats=templates.summary()))
            # A lookup that returns the same SQL but has effects is not skipped.
            model = get_model(*r['facts']['model'].split('.')); pk = model._meta.pk
            effects = []
            class EffectfulExact(IntegerFieldExact):
                def as_sql(self, compiler, connection):
                    effects.append(self.rhs)
                    return super().as_sql(compiler, connection)
            pk.register_lookup(EffectfulExact)
            try:
                original = evaluate(r, 7)
                with installed() as templates:
                    candidate = evaluate(r, 7)
                check('custom lookup effects retained on original path', original == candidate and effects == [7, 7]
                      and templates.fallbacks['lookup-or-value'] == 1 and not templates.templates)
                controls.append(dict(consumer='effectful-lookup', effects=effects, stats=templates.summary()))
            finally:
                pk._unregister_lookup(EffectfulExact)
            base.save('offline-controls.json', controls)
            check('no offline DB connection', connection.connection is None)
        base.save('offline-result.json', dict(status='passed', checks=checks))
        return

    enabled = os.environ['RDB_TEMPLATE_ARM'] == 'candidate'
    if sys.argv[1] == 'replay':
        original_place = OrderCreator.place_order
        def place(self, *args, **kwargs):
            STATE['placements'] += 1
            STATE['active'] = STATE['placements'] <= 4
            try:
                return original_place(self, *args, **kwargs)
            finally:
                STATE['active'] = False
        with (installed(lambda: STATE['active']) if enabled else nullcontext()) as templates:
            with patch.object(OrderCreator, 'place_order', place), patch.object(base, 'profile', profile):
                base.main()
        base.save('specialization.json', dict(arm=os.environ['RDB_TEMPLATE_ARM'], counts=dict(counts),
                                            templates=templates.summary() if templates else None))
        check('compiler restored after experiment', SQLCompiler.as_sql is ORIGINAL)
        if enabled:
            check('six cold shapes and forty actual bindings', templates.counts['cold_compilations'] == 6 and templates.counts['hits'] == 40)
        base.save('template-result.json', dict(status='passed', checks=checks))
    else:
        Partner = get_model('partner', 'Partner'); Stock = get_model('partner', 'StockRecord')
        initial = base.snapshot(connection)
        objects = []; init_events = []
        def init(sender, instance, **kwargs):
            objects.append(instance)
            init_events.append(dict(model=instance._meta.label,
                                    fields=base.encode({f.attname: instance.__dict__[f.attname] for f in instance._meta.concrete_fields if f.attname in instance.__dict__})))
        def fresh_reads():
            a = Partner._base_manager.get(pk=1); b = Partner._base_manager.get(pk=1)
            Partner._base_manager.filter(pk=1).update(name='new value after compilation')
            c = Partner._base_manager.get(pk=1)
            return dict(distinct=a is not b and b is not c, old=a.name, new=c.name)
        def partial_reads():
            a = Stock._base_manager.only('num_allocated').get(pk=1)
            deferred = sorted(a.get_deferred_fields())
            price = a.price  # Original deferred-field load on the same object.
            b = Stock._base_manager.get(pk=1)
            return dict(deferred_before=deferred, deferred_after=sorted(a.get_deferred_fields()), price=base.encode(price),
                        full_deferred=sorted(b.get_deferred_fields()), distinct=a is not b)
        def signal_failure():
            Partner._base_manager.get(pk=1)
            def fail(sender, instance, **kwargs):
                raise RuntimeError('original post_init failure')
            signals.post_init.connect(fail, sender=Partner, weak=False)
            try:
                Partner._base_manager.get(pk=1)
            finally:
                signals.post_init.disconnect(fail, sender=Partner)
        cases = [
            ('fresh-reads-after-write', fresh_reads), ('deferred-and-full-mapping', partial_reads),
            ('missing', lambda: Partner._base_manager.get(pk=99999999)),
            ('null', lambda: Partner._base_manager.get(pk=None)),
            ('overflow', lambda: Partner._base_manager.get(pk=2**63)),
            ('invalid', lambda: Partner._base_manager.get(pk='not-an-integer')),
            ('post-init-failure', signal_failure),
            ('values-and-tuples', lambda: dict(values=Partner._base_manager.values('id', 'name').get(pk=1),
                                              tuples=Partner._base_manager.values_list('id', 'name').get(pk=1))),
            ('locking', lambda: dict(name=Partner._base_manager.select_for_update().get(pk=1).name)),
        ]
        results = []
        with (installed(lambda: STATE['active']) if enabled else nullcontext()) as templates:
            for label, action in cases:
                init_events.clear(); objects.clear(); start = len(base.EVENTS)
                with transaction.atomic():
                    signals.post_init.connect(init, sender=Partner, weak=False)
                    signals.post_init.connect(init, sender=Stock, weak=False)
                    STATE['active'] = base.ACTIVE = True
                    try:
                        value = action(); error = None
                    except Exception as exc:
                        value = None; error = dict(type=type(exc).__qualname__, message=str(exc))
                    finally:
                        STATE['active'] = base.ACTIVE = False
                        signals.post_init.disconnect(init, sender=Partner)
                        signals.post_init.disconnect(init, sender=Stock)
                    trace = [dict(sql=e['sql'], params=e['params']) for e in base.EVENTS[start:] if e['kind'] == 'execute']
                    base.save(label + '-full-state.json', base.snapshot(connection))
                    results.append(dict(case=label, value=base.encode(value), error=error, model_events=list(init_events), sql=trace))
                    transaction.set_rollback(True)
        base.save('local-observations.json', dict(cases=results, templates=templates.summary() if templates else None))
        check('all local data and sequences restored', base.snapshot(connection) == initial)
        check('read-after-write and fresh identities observed', results[0]['value']['distinct'] and results[0]['value']['new'] == 'new value after compilation')
        check('signal exception observed', results[6]['error'] == dict(type='RuntimeError', message='original post_init failure'))
        check('locking SQL retained', any('FOR UPDATE' in e['sql'] for e in results[-1]['sql']))
        if enabled:
            check('real local template bindings exercised', templates.counts['hits'] >= 3)
        base.save('local-result.json', dict(status='passed', checks=checks, cases=results, templates=templates.summary() if templates else None))
    connection.close()


if __name__ == '__main__':
    main()
