"""Observe original SELECT construction and its result ABI, without optimization."""
from collections import Counter, defaultdict
import cProfile
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import types
from unittest.mock import patch

import baseline_probe as base

OUT = Path(sys.argv[2])
STATE = dict(active=False, nested=False, order=None, calls=0)
RECORDS = []
QUERIES = []  # Strong references prevent query id reuse in the provenance graph.
QUERY_IDS = {}
CLONES = []
CODES = {}


def qid(query):
    key = id(query)
    if key not in QUERY_IDS:
        QUERY_IDS[key] = len(QUERIES)
        QUERIES.append(query)
    return QUERY_IDS[key]


def code_id(code):
    key = str(id(code))
    CODES[key] = code
    return key


def context():
    frames = []
    frame = sys._getframe(2)
    while frame:
        module = frame.f_globals.get('__name__', '')
        if module.startswith(('oscar.', 'django.')):
            frames.append(dict(module=module, code=code_id(frame.f_code), offset=frame.f_lasti))
        frame = frame.f_back
    names = [CODES[f['code']].co_qualname for f in frames if f['module'].startswith('oscar.')]
    return dict(stack=frames, owner=names[0] if names else 'framework',
                phase=next((n for n in reversed(names) if n != 'OrderCreator.place_order'), 'OrderCreator.place_order (direct)'))


def column(expr):
    from django.db.models.expressions import Col
    if type(expr) is not Col:
        return dict(kind=type(expr).__module__ + '.' + type(expr).__qualname__)
    f = expr.target
    return dict(kind='Col', model=f.model._meta.label, field=f.name, attname=f.attname,
                column=f.column, alias=expr.alias, field_type=type(f).__name__, primary_key=f.primary_key)


def abi(compiler):
    def klass(info):
        if info is None:
            return None
        result = {}
        for key, value in info.items():
            if key == 'model':
                result[key] = value._meta.label
            elif key == 'related_klass_infos':
                result[key] = [klass(v) for v in value]
            else:
                result[key] = base.encode(value)
        return result
    return dict(select=[dict(expression=column(s[0]), sql=s[1][0], params=base.encode(s[1][1]), alias=s[2])
                        for s in compiler.select or []],
                klass_info=klass(compiler.klass_info), annotation_col_map=compiler.annotation_col_map,
                col_count=compiler.col_count)


def shape(compiler):
    from django.db.models.lookups import IntegerFieldExact
    from django.db.models.sql.where import WhereNode
    query = compiler.query
    def where(node):
        if type(node) is WhereNode:
            return dict(connector=node.connector, negated=node.negated, children=[where(c) for c in node.children])
        return dict(kind=type(node).__name__, lhs=column(node.lhs), rhs=base.encode(node.rhs))
    facts = dict(model=query.model._meta.label, where=where(query.where), aliases=list(query.alias_map),
                 low_mark=query.low_mark, high_mark=query.high_mark, select_for_update=query.select_for_update,
                 order_by=base.encode(query.order_by), default_ordering=query.default_ordering,
                 distinct=query.distinct, combinator=query.combinator, annotation_count=len(query.annotations),
                 select_related=base.encode(query.select_related), result_abi=abi(compiler))
    children = query.where.children
    # A screening predicate, NOT a deletion/admission rule or runtime patch.
    facts['integer_pk_point_candidate'] = bool(
        len(children) == 1 and type(children[0]) is IntegerFieldExact
        and column(children[0].lhs).get('primary_key') and type(children[0].rhs) is int
        and query.where.connector == 'AND' and not query.where.negated
        and len(query.alias_map) == 1 and not query.select_for_update and not query.order_by
        and not query.default_ordering and not query.distinct and not query.combinator
        and not query.annotations and not query.select_related and query.low_mark == 0 and query.high_mark == 21
        and all(x['expression']['kind'] == 'Col' for x in facts['result_abi']['select']))
    return facts


def root_probe(original, kind):
    def observed(self, *args, **kwargs):
        if not STATE['active'] or STATE['nested']:
            return original(self, *args, **kwargs)
        query = self.query if kind == 'select_compile' else self
        record = dict(kind=kind, query=qid(query), order=STATE['order'], **context())
        profiler = cProfile.Profile(timer=time.thread_time_ns, timeunit=1e-9)
        old_profile = sys.getprofile()
        STATE['nested'] = True
        try:
            profiler.enable()
            result = original(self, *args, **kwargs)
        except BaseException as error:
            record['error'] = dict(type=type(error).__name__, message=str(error))
            raise
        finally:
            profiler.disable()
            sys.setprofile(old_profile)
            STATE['nested'] = False
            record['profiler'] = profiler
            RECORDS.append(record)
        if kind == 'select_compile':
            record.update(sql=result[0], params=base.encode(result[1]), facts=shape(self))
        return result
    return observed


def main():
    import django
    django.setup()
    from django.db import connection, connections
    from django.db.models.query import QuerySet, ModelIterable
    from django.db.models.sql.query import Query
    from django.db.models.sql.compiler import SQLCompiler
    from django.db.models.lookups import IntegerFieldExact, IntegerFieldOverflow, Lookup
    from django.db.models.fields import IntegerField
    from oscar.apps.order.utils import OrderCreator
    from oscar.core.loading import get_model

    original_clone = Query.clone
    original_place = OrderCreator.place_order

    def clone(self):
        result = original_clone(self)
        if STATE['active']:
            CLONES.append(dict(source=qid(self), target=qid(result)))
        return result

    def place(self, *args, **kwargs):
        STATE['calls'] += 1
        # Fixed four successful placements only; baseline error-prefix checks
        # still run but are deliberately excluded from this cost dataset.
        STATE.update(active=STATE['calls'] <= 4, order=kwargs.get('order_number'))
        try:
            return original_place(self, *args, **kwargs)
        finally:
            STATE['active'] = False

    bound = {
        'Query.build_filter': Query.build_filter, 'Query.build_lookup': Query.build_lookup,
        'Query.clone': Query.clone, 'SQLCompiler.as_sql': SQLCompiler.as_sql,
        'SQLCompiler.get_select': SQLCompiler.get_select, 'SQLCompiler.compile': SQLCompiler.compile,
        'QuerySet.get': QuerySet.get, 'ModelIterable.__iter__': ModelIterable.__iter__,
        'Lookup.__init__': Lookup.__init__, 'IntegerFieldOverflow.process_rhs': IntegerFieldOverflow.process_rhs,
        'IntegerField.get_prep_value': IntegerField.get_prep_value,
        'OrderCreator.place_order': OrderCreator.place_order,
    }
    for f in bound.values():
        code_id(f.__code__)
    with (patch.object(Query, 'clone', clone), patch.object(Query, 'build_filter', root_probe(Query.build_filter, 'build_filter')),
         patch.object(SQLCompiler, 'as_sql', root_probe(SQLCompiler.as_sql, 'select_compile')),
         patch.object(OrderCreator, 'place_order', place)):
        base.main()

    checks = []
    def check(name, ok):
        assert ok, name
        checks.append(name)
    selects = [r for r in RECORDS if r['kind'] == 'select_compile']
    check('exactly 114 SELECT compilations observed', len(selects) == 114 and all('error' not in r for r in selects))
    events = [e for n in ['RDB-ONE', 'RDB-THREE', 'RDB-SIX', 'RDB-DIGITAL']
              for e in json.loads((OUT / (n + '-trace.json')).read_text())
              if e.get('sql', '').startswith('SELECT')]
    check('every compiled SQL/parameter pair equals actual driver order',
          [(r['sql'], r['params']) for r in selects] == [(e['sql'], e['params']) for e in events])

    # Disjoint root profiles: SQL compilation and outer build_filter scopes do
    # not overlap. The wrappers' metadata/serialization is outside cProfile.
    for r in RECORDS:
        entries = r.pop('profiler').getstats()
        functions = []
        for item in entries:
            native = isinstance(item.code, str)
            key = 'native:' + item.code if native else code_id(item.code)
            functions.append(dict(code=key, calls=item.callcount, recursive_calls=item.reccallcount,
                                  self_cpu_ms=item.inlinetime * 1000, inclusive_cpu_ms=item.totaltime * 1000))
        r['functions'] = functions
        r['self_cpu_ms'] = sum(f['self_cpu_ms'] for f in functions)

    groups = defaultdict(list)
    for r in selects:
        key = hashlib.sha256(json.dumps([r['sql'], r['facts']['result_abi']], sort_keys=True).encode()).hexdigest()
        groups[key].append(r)
    summary = []
    for key, rows in groups.items():
        first = rows[0]
        check('one stable semantic screen per shape ' + key[:8], len({r['facts']['integer_pk_point_candidate'] for r in rows}) == 1)
        summary.append(dict(shape=key, calls=len(rows), model=first['facts']['model'], sql=first['sql'],
                            owners=dict(Counter(r['owner'] for r in rows)),
                            compile_self_cpu_ms=sum(r['self_cpu_ms'] for r in rows),
                            point_candidate=first['facts']['integer_pk_point_candidate'],
                            result_abi=first['facts']['result_abi']))
    summary.sort(key=lambda r: -r['compile_self_cpu_ms'])
    candidate_groups = [g for g in summary if g['point_candidate']]
    check('six source-supported integer primary-key shapes / 46 calls',
          len(candidate_groups) == 6 and sum(g['calls'] for g in candidate_groups) == 46)

    # Determine which original filter construction queries reach each SELECT
    # through clone edges. Shared ancestors have multiple consumers; never sum
    # one build_filter cost once per consumer.
    descendants = defaultdict(set)
    for edge in CLONES:
        descendants[edge['source']].add(edge['target'])
    def reachable(start):
        seen, pending = {start}, [start]
        while pending:
            for target in descendants[pending.pop()]:
                if target not in seen:
                    seen.add(target)
                    pending.append(target)
        return seen
    for r in RECORDS:
        if r['kind'] == 'build_filter':
            children = reachable(r['query'])
            consumers = [i for i, s in enumerate(selects) if s['query'] in children]
            r['select_consumers'] = consumers
            r['consumer_class'] = ('no-observed-select' if not consumers else
                                   'point-only' if all(selects[i]['facts']['integer_pk_point_candidate'] for i in consumers) else
                                   'other-or-mixed-select')

    # Persist the expensive original observation before offline boundary checks.
    # An assertion about a field domain must not discard completed raw profiles.
    base.save('construction-records.json', RECORDS)
    base.save('query-clones.json', CLONES)
    base.save('construction-codes.json', {key: dict(name=code.co_qualname, file=code.co_filename,
                                                   hash=base.code_hash(code)) for key, code in CODES.items()})

    # Validate the shape boundary using original Django only; no candidate is
    # installed or timed. EmptyResultSet / None / invalid input are retained.
    boundary = []
    with patch.object(type(connections['default']), 'cursor', side_effect=AssertionError('Shape checks must not execute SQL')):
        for g in candidate_groups:
            model = get_model(*g['model'].split('.'))
            fields = [s['expression']['field'] for s in g['result_abi']['select']]
            all_fields = [f.name for f in model._meta.concrete_fields]
            minimum, maximum = connection.ops.integer_field_range(model._meta.pk.get_internal_type())
            g['pk_domain'] = dict(field_type=model._meta.pk.get_internal_type(), minimum=minimum, maximum=maximum)
            cases = [('unseen', 314159), ('zero', 0), ('negative', -1), ('max-field', maximum),
                     ('overflow', maximum+1), ('underflow', minimum-1), ('null', None), ('invalid', 'not-an-integer')]
            for label, value in cases:
                try:
                    qs = model._base_manager.filter(pk=value).order_by()
                    if fields != all_fields:
                        qs = qs.only(*fields)
                    qs.query.set_limits(high=21)
                    compiler = qs.query.get_compiler(using='default')
                    sql, params = compiler.as_sql()
                    result = dict(sql=sql, params=base.encode(params), result_abi=abi(compiler))
                except Exception as error:
                    result = dict(error=type(error).__name__, message=str(error))
                if label in ['unseen', 'zero', 'negative', 'max-field']:
                    check(g['model'] + ' ' + label + ' stable SQL and result ABI',
                          result.get('sql') == g['sql'] and result.get('result_abi') == g['result_abi'] and result.get('params') == [value])
                elif label in ['overflow', 'underflow']:
                    check(g['model'] + ' ' + label + ' original empty-query shortcut', result.get('error') == 'EmptyResultSet')
                elif label == 'null':
                    check(g['model'] + ' NULL changes query structure', 'IS NULL' in result.get('sql', '') and result['sql'] != g['sql'])
                else:
                    check(g['model'] + ' invalid conversion retained', result.get('error') == 'ValueError')
                boundary.append(dict(shape=g['shape'], case=label, input=value, result=result))

    code_records = {}
    for key, code in CODES.items():
        code_records[key] = dict(name=code.co_qualname, file=code.co_filename, hash=base.code_hash(code))
    # Bind recovered live target functions to the already retained .pyc, not
    # merely to the checked-out source or a wrapper name.
    import marshal
    import dis
    profile = Path(os.environ['RDB_SELECT_PROFILE'])
    witnesses = []
    def walk(code):
        yield code
        for c in code.co_consts:
            if isinstance(c, types.CodeType):
                yield from walk(c)
    for name, f in bound.items():
        p = profile / 'profiles/observed-pyc' / (f.__module__ + '.pyc')
        candidates = {base.code_hash(c): c for c in walk(marshal.loads(p.read_bytes()[16:]))}
        h = base.code_hash(f.__code__)
        check('original code matches retained pyc ' + name, h in candidates)
        witnesses.append(dict(name=name, module=f.__module__, hash=h,
                              instructions=[dict(offset=i.offset, op=i.opname, arg=i.argrepr) for i in dis.get_instructions(candidates[h])]))
    by_owner = defaultdict(lambda: dict(calls=0, cpu_ms=0))
    for r in RECORDS:
        key = (r['kind'], r['owner'])
        by_owner[key]['calls'] += 1
        by_owner[key]['cpu_ms'] += r['self_cpu_ms']
    filters = [r for r in RECORDS if r['kind'] == 'build_filter']
    total_compile = sum(r['self_cpu_ms'] for r in selects)
    point_compile = sum(g['compile_self_cpu_ms'] for g in candidate_groups)
    result = dict(status='passed', checks=checks, shapes=summary, compile_cpu_ms=total_compile,
                  point_compile_cpu_ms=point_compile, point_share_of_observed_compile_cpu=point_compile/total_compile,
                  build_filter_roots=len(filters), build_filter_cpu_ms=sum(r['self_cpu_ms'] for r in filters),
                  point_only_filter_cpu_ms=sum(r['self_cpu_ms'] for r in filters if r['consumer_class'] == 'point-only'),
                  filters_by_consumer=dict(Counter(r['consumer_class'] for r in filters)),
                  owner_costs=[dict(kind=k[0], owner=k[1], **v) for k, v in by_owner.items()],
                  optimizer_modified=False, application_optimized=False, performance_claim=None,
                  limits=['One original diagnostic replay; no performance comparison or statistical precision claim',
                          'cProfile CPU is perturbed; scopes exclude outer QuerySet cloning and result conversion',
                          'Shared query ancestors are attributed once; unlinked filters are not guessed to be SELECTs',
                          'Shape checks use original compiler only; stable samples do not prove arbitrary-input safety',
                          'SQL plus parameters alone is insufficient: model mapping/deferred fields and converters must be retained'])
    for filename, data in [('select-analysis.json', result), ('construction-records.json', RECORDS),
                           ('query-clones.json', CLONES), ('construction-codes.json', code_records),
                           ('select-bytecode.json', witnesses), ('shape-boundaries.json', boundary)]:
        base.save(filename, data)
    connection.close()


if __name__ == '__main__':
    main()
