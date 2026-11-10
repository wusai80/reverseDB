"""Bounded original Oscar sandbox witness, not an optimized implementation."""
import collections
import datetime
import decimal
import dis
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import random
import sys
import types
import weakref

import psycopg2.extensions

OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
EVENTS = []
ACTIVE = False
CODES = {}
OBJECTS = []  # Keep observed instances alive: identity tokens must not reuse addresses.


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, decimal.Decimal)):
        return {"type": type(value).__name__, "value": str(value)}
    if isinstance(value, (list, tuple)):
        return [encode(x) for x in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    return {"type": type(value).__module__ + "." + type(value).__qualname__}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def canonical_code(code):
    # marshal encodes interning/reference bookkeeping that may differ between
    # a live object and a freshly unmarshalled object. Hash all code content,
    # including nested constants and exception/line tables, independently.
    def const(value):
        if isinstance(value, types.CodeType):
            return ['code', canonical_code(value)]
        if isinstance(value, (tuple, frozenset)):
            items = [const(v) for v in value]
            return [type(value).__name__, sorted(items, key=repr) if isinstance(value, frozenset) else items]
        if value is None or value is Ellipsis or type(value) in (bool, int, float, complex, str, bytes):
            return [type(value).__name__, repr(value)]
        raise TypeError(type(value))
    fields = ['co_filename', 'co_name', 'co_qualname', 'co_firstlineno', 'co_argcount',
              'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals', 'co_stacksize',
              'co_flags', 'co_varnames', 'co_names', 'co_freevars', 'co_cellvars']
    result = {name: getattr(code, name) for name in fields}
    result.update({name: getattr(code, name).hex() for name in ['co_code', 'co_linetable', 'co_exceptiontable']})
    result['co_consts'] = [const(c) for c in code.co_consts]
    return result


def code_hash(code):
    return digest(json.dumps(canonical_code(code), sort_keys=True, ensure_ascii=True).encode())


def code_id(code):
    key = code_hash(code)
    if key not in CODES:
        CODES[key] = dict(file=code.co_filename, name=code.co_qualname,
                         first_line=code.co_firstlineno,
                         instructions=[dict(offset=i.offset, op=i.opname, arg=i.argrepr)
                                       for i in dis.get_instructions(code, show_caches=True)],
                         exception_table=code.co_exceptiontable.hex())
    return key


def stack():
    frame = sys._getframe(2)
    frames = []
    while frame:
        if frame.f_globals.get('__name__', '').startswith(('oscar.', 'django.')):
            frames.append(dict(code=code_id(frame.f_code), offset=frame.f_lasti,
                               line=frame.f_lineno))
        frame = frame.f_back
    return frames


class TraceCursor(psycopg2.extensions.cursor):
    def _run(self, method, query, params):
        if not ACTIVE:
            return getattr(super(), method)(query, params)
        event = dict(kind=method, sql=query.decode() if isinstance(query, bytes) else str(query),
                     params=encode(params), stack=stack(),
                     tx_before=self.connection.get_transaction_status())
        EVENTS.append(event)
        try:
            result = getattr(super(), method)(query, params)
            event.update(rowcount=self.rowcount, status=self.statusmessage)
            return result
        except BaseException as error:
            event['exception'] = type(error).__module__ + '.' + type(error).__name__
            raise
        finally:
            event['tx_after'] = self.connection.get_transaction_status()

    def execute(self, query, vars=None):
        return self._run('execute', query, vars)

    def executemany(self, query, vars_list):
        return self._run('executemany', query, vars_list)


class TraceConnection(psycopg2.extensions.connection):
    def commit(self):
        if ACTIVE:
            EVENTS.append(dict(kind='commit', stack=stack()))
        return super().commit()

    def rollback(self):
        if ACTIVE:
            EVENTS.append(dict(kind='rollback', stack=stack()))
        return super().rollback()


def identity(obj):
    for i, prior in enumerate(OBJECTS):
        if prior is obj:
            return i
    OBJECTS.append(obj)
    return len(OBJECTS) - 1


WATCH = {'allocate', 'pre_save_signal', 'post_save_signal', 'update_stock_alerts',
         'receive_order_placed', 'refresh_from_db', 'from_db', 'create_additional_line_models'}


def profile(frame, event, arg):
    if event not in ('call', 'return') or frame.f_code.co_name not in WATCH:
        return
    module = frame.f_globals.get('__name__', '')
    if not module.startswith(('oscar.', 'django.')):
        return
    obj = frame.f_locals.get('self', frame.f_locals.get('instance'))
    data = dict(kind='python_' + event, module=module, code=code_id(frame.f_code))
    if hasattr(obj, '_meta') and not isinstance(obj, type):
        data.update(identity=identity(obj), model=obj._meta.label,
                    fields=encode({f.attname: obj.__dict__[f.attname]
                                   for f in obj._meta.concrete_fields if f.attname in obj.__dict__}))
    if event == 'return' and hasattr(arg, '_meta') and not isinstance(arg, type):
        data['return_identity'] = identity(arg)
    EVENTS.append(data)


def snapshot(connection):
    state = dict(tables={}, sequences={})
    with connection.cursor() as c:
        c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        for (table,) in c.fetchall():
            q = connection.ops.quote_name(table)
            c.execute(f'SELECT to_jsonb(t)::text FROM {q} t ORDER BY to_jsonb(t)::text')
            state['tables'][table] = [json.loads(row[0]) for row in c.fetchall()]
        c.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        for (seq,) in c.fetchall():
            c.execute('SELECT last_value, is_called FROM ' + connection.ops.quote_name(seq))
            state['sequences'][seq] = list(c.fetchone())
    return state


def main(allocation_statement_prefix='UPDATE "partner_stockrecord"'):
    global ACTIVE
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS'] = dict(cursor_factory=TraceCursor,
                                                    connection_factory=TraceConnection)
    django.setup()
    from django.apps import apps
    from django.db import connection, transaction
    from django.db.models import signals
    from django.contrib.auth import get_user_model
    from freezegun import freeze_time
    from oscar.core.loading import get_model, get_class
    from oscar.apps.order.utils import OrderCreator
    from oscar.apps.order.signals import order_placed
    from oscar.apps.checkout.calculators import OrderTotalCalculator
    from oscar.apps.shipping.methods import Free
    from oscar.test import factories
    import factory.random
    random.seed(20261106)
    factory.random.reseed_random(20261106)
    Basket = get_model('basket', 'Basket')
    StockRecord = get_model('partner', 'StockRecord')
    Product = get_model('catalogue', 'Product')
    Strategy = get_class('partner.strategy', 'Selector')
    SurchargeApplicator = get_class('checkout.applicator', 'SurchargeApplicator')

    if sys.argv[1] == 'prepare':
        with freeze_time('2026-11-06 00:00:00+00:00'):
            users = [get_user_model().objects.create_user(username=f'research-user-{i}') for i in range(2)]
            pc = factories.ProductClassFactory(name='Physical goods')
            products = [factories.ProductFactory(title=f'Complete product {i}', upc=f'RDB-{i}',
                        product_class=pc, stockrecords__num_in_stock=5 if i == 0 else 100,
                        stockrecords__num_allocated=None if i == 0 else 0,
                        stockrecords__low_stock_threshold=3 if i == 0 else 1,
                        stockrecords__price=decimal.Decimal('10.17') + i) for i in range(8)]
            digital_pc = factories.ProductClassFactory(name='Digital goods', track_stock=False, requires_shipping=False)
            products.append(factories.ProductFactory(title='Digital product', upc='RDB-D', product_class=digital_pc))
            option = factories.OptionFactory(name='Message', code='message')
            baskets = []
            for i, indexes in enumerate([[0], [2, 1, 0], [5, 4, 3, 2, 1, 0], [8], []]):
                b = Basket.objects.create(owner=users[i % 2])
                b.strategy = Strategy().strategy(user=users[i % 2])
                for j, ix in enumerate(indexes):
                    b.add_product(products[ix], quantity=1, options=[dict(option=option, value='完整数据 café')] if j == 0 else [])
                baskets.append(b.pk)
            save('fixture-request.json', dict(baskets=baskets, users=[u.pk for u in users],
                 order_numbers=['RDB-ONE', 'RDB-THREE', 'RDB-SIX', 'RDB-DIGITAL'],
                 seed=20261106, inputs='fixed complete small fixture; not a restriction on removable data'))
        save('fixture-full-state.json', snapshot(connection))
        connection.close()
        return

    checks = []
    def check(name, ok):
        assert ok, name
        checks.append(name)

    protocol = json.loads((OUT.parent / 'fixture-request.json').read_text())
    before = snapshot(connection)
    save('before-full-state.json', before)
    check('complete fixture restored', before == json.loads((OUT.parent / 'fixture-full-state.json').read_text()))
    runtime = dict(python=sys.version, django=django.get_version(), psycopg2=psycopg2.__version__,
                   settings=settings.SETTINGS_MODULE, atomic_requests=settings.DATABASES['default']['ATOMIC_REQUESTS'],
                   models={m._meta.label: m.__module__ + '.' + m.__qualname__ for m in apps.get_models()})
    with connection.cursor() as c:
        c.execute('SHOW transaction_isolation'); runtime['isolation'] = c.fetchone()[0]
        c.execute('SELECT version()'); runtime['postgres'] = c.fetchone()[0]
    # Inspect live original callbacks before observation; no signal is disconnected.
    receivers = {}
    for signal_name, signal in [('pre_init', signals.pre_init), ('post_init', signals.post_init),
                                ('pre_save', signals.pre_save), ('post_save', signals.post_save), ('order_placed', order_placed)]:
        entries = []
        for lookup, receiver, is_async in signal.receivers:
            target = receiver() if isinstance(receiver, weakref.ReferenceType) else receiver
            if target is not None:
                entries.append(dict(target=target.__module__ + '.' + target.__qualname__,
                                    lookup=repr(lookup), is_async=is_async))
        receivers[signal_name] = entries
    save('receivers.json', receivers)
    save('runtime.json', runtime)

    results = []
    with freeze_time('2026-11-06 01:00:00+00:00'):
        for i, number in enumerate(protocol['order_numbers']):
            user = get_user_model().objects.get(pk=protocol['users'][i % 2])
            b = Basket.objects.get(pk=protocol['baskets'][i])
            b.strategy = Strategy().strategy(user=user)
            shipping = Free()
            shipping_charge = shipping.calculate(b)
            surcharges = SurchargeApplicator().get_applicable_surcharges(b)
            total = OrderTotalCalculator().calculate(basket=b, shipping_charge=shipping_charge, surcharges=surcharges)
            lines = list(b.all_lines())
            stocks = [line.stockrecord for line in lines]
            args = dict(basket=b, total=total, shipping_method=shipping, shipping_charge=shipping_charge,
                        user=user, order_number=number, surcharges=surcharges)
            start = len(EVENTS)
            # Match sandbox ATOMIC_REQUESTS transaction scope, without claiming HTTP checkout coverage.
            ACTIVE = True
            sys.setprofile(profile)
            try:
                with transaction.atomic():
                    order = OrderCreator().place_order(**args)
            finally:
                sys.setprofile(None)
                ACTIVE = False
            trace = EVENTS[start:]
            save(number + '-trace.json', trace)
            check(number + ' original basket order preserved', list(order.lines.values_list('product_id', flat=True)) == [l.product_id for l in lines])
            check(number + ' original stock object identities preserved', all(l.stockrecord is s for l, s in zip(lines, stocks)))
            check(number + ' stock in-memory refresh matches database', all(s.num_allocated == StockRecord.objects.get(pk=s.pk).num_allocated for s in stocks))
            alloc_sql = [e for e in trace if e['kind'] == 'execute' and e['sql'].startswith(allocation_statement_prefix)]
            expected_order = sorted(l.stockrecord_id for l in lines if l.product.get_product_class().track_stock)
            check(number + ' stock UPDATEs in ascending lock order', [e['params'][-1] for e in alloc_sql] == expected_order)
            check(number + ' original order analytics receiver executed', any(e.get('module') == 'oscar.apps.analytics.receivers' for e in trace))
            sql = [e for e in trace if e['kind'] in ('execute', 'executemany')]
            results.append(dict(number=number, order=encode({f.attname: getattr(order, f.attname) for f in order._meta.concrete_fields}),
                                lines=len(lines), stock_allocations=len(alloc_sql), execute_count=len(sql),
                                sql_verbs=dict(collections.Counter(e['sql'].split()[0] for e in sql)),
                                stock_identities=[identity(s) for s in stocks]))

        # Error prefix cases must not change rows or sequences: both fail before creation.
        for label, basket_id, number in [('empty', protocol['baskets'][-1], 'RDB-EMPTY'),
                                         ('duplicate', protocol['baskets'][0], protocol['order_numbers'][0])]:
            b = Basket.objects.get(pk=basket_id); b.strategy = Strategy().strategy()
            state = snapshot(connection)
            start = len(EVENTS); ACTIVE = True
            try:
                with transaction.atomic():
                    OrderCreator().place_order(basket=b, total=None, shipping_method=None,
                                               shipping_charge=None, order_number=number)
            except ValueError as error:
                message = str(error)
            else:
                raise AssertionError(label + ' did not reject')
            finally:
                ACTIVE = False
            save(label + '-trace.json', EVENTS[start:])
            check(label + ' exact original error and unchanged complete database',
                  message == ('Empty baskets cannot be submitted' if label == 'empty' else 'There is already an order with number RDB-ONE')
                  and snapshot(connection) == state)

    check('stock alert callback effect retained', get_model('partner', 'StockAlert').objects.filter(status='Open').count() == 1)
    check('all nine fixture products retained', Product.objects.count() == 9)
    after = snapshot(connection); save('after-full-state.json', after)
    # Actual executing code objects are matched against retained on-disk .pyc objects.
    modules = ['oscar.apps.order.utils', 'oscar.apps.partner.abstract_models']
    witness = []
    def walk(code):
        yield code
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from walk(const)
    for name in modules:
        module = sys.modules[name]
        raw = Path(module.__cached__).read_bytes()
        check(name + ' checked-hash bytecode', int.from_bytes(raw[4:8], 'little') == 3)
        check(name + ' bytecode source hash matches', raw[8:16] == importlib.util.source_hash(Path(module.__file__).read_bytes()))
        codes = {code_hash(c): c for c in walk(marshal.loads(raw[16:]))}
        observed = sorted(set(codes) & set(CODES))
        check(name + ' actual execution bound to pyc', bool(observed))
        (OUT / (name + '.pyc')).write_bytes(raw)
        witness.append(dict(module=name, source=module.__file__, source_sha256=digest(Path(module.__file__).read_bytes()),
                            pyc_sha256=digest(raw), observed_code_hashes=observed))
    check('all six selected target methods observed',
          set(['OrderCreator.place_order', 'OrderCreator.create_line_models', 'OrderCreator.create_line_price_models',
               'OrderCreator.create_line_attributes', 'OrderCreator.update_stock_records', 'AbstractStockRecord.allocate'])
          <= {CODES[k]['name'] for w in witness for k in w['observed_code_hashes']})
    save('bytecode-witness.json', witness)
    save('executed-code.json', CODES)
    save('loaded-sources.json', {name: dict(path=m.__file__, sha256=digest(Path(m.__file__).read_bytes()))
         for name, m in sorted(sys.modules.items()) if getattr(m, '__file__', None)
         and Path(m.__file__).is_file() and name.startswith(('oscar.', 'django.', 'psycopg2'))})
    save('result.json', dict(status='passed', checks=checks, orders=results,
         table_count=len(after['tables']), sequence_count=len(after['sequences']),
         limits=['direct OrderCreator with sandbox configuration and request-equivalent atomic scope; not full HTTP checkout',
                 'controlled source-to-pyc witness, not independently published binary or decompilation roundtrip',
                 'fixed small fixture; no general semantic proof or performance comparison']))
    connection.close()


if __name__ == '__main__':
    main()
