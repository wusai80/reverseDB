"""Isolated, frozen-Django experiment: cache SELECT structure, never row data.

The first eligible compilation produces SQL and a result-layout template using
the original compiler. Hits keep the original WHERE compiler and downstream
execution/decoding/ModelIterable. Query/filter construction is not bypassed.
This is a manually implemented generic staging adapter, not a production gate.
"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from types import MethodType

from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.backends.postgresql.compiler import SQLCompiler as PgCompiler
from django.db.models import AutoField, BigAutoField, Field
from django.db.models.fields.related import ForeignKey, OneToOneField
from django.db.models.expressions import Col
from django.db.models.lookups import IntegerFieldExact
from django.db.models.sql.compiler import SQLCompiler
from django.db.models.sql.datastructures import BaseTable
from django.db.models.sql.query import Query
from django.db.models.sql.where import WhereNode

ORIGINAL = SQLCompiler.as_sql
COMPILE = SQLCompiler.compile
GET_SELECT = SQLCompiler.get_select
FIELD_COL = Field.get_col
FIELD_FORMAT = Field.select_format
FOREIGN_COL = ForeignKey.get_col
ACTIVE = ContextVar('select_template_experiment', default=None)
INSTALL_LOCK = threading.RLock()

# Unexpected query state is kept on the original path. This is a source-bound
# shape guard, not evidence that modules outside this shape are unnecessary.
QUERY_KEYS = frozenset('model alias_refcount alias_map alias_cols external_aliases table_map used_aliases where annotations extra _filtered_relations _annotation_select_cache _extra_select_cache filter_is_sticky _lookup_joins order_by extra_order_by default_ordering high_mark low_mark deferred_loading base_table select_related select_for_update select_for_update_nowait select_for_update_skip_locked select_for_update_of select_for_no_key_update distinct distinct_fields group_by subquery extra_tables combinator combinator_all combined_queries annotation_select_mask extra_select_mask select selected values_select'.split())


@dataclass(frozen=True)
class Template:
    sql: str
    where_sql: str
    # Include original output_field: ForeignKey columns decode through the
    # target field, not necessarily through the projected field itself.
    columns: tuple
    model: type
    select_fields: tuple

    def hydrate(self, compiler):
        # Fresh containers and Col nodes. Never hand a mutable cached layout to
        # a consumer. Field/model metadata is the original frozen framework ABI.
        compiler.select = [(Col(alias, field, output), (sql, []), label)
                           for alias, field, output, sql, label in self.columns]
        compiler.klass_info = dict(model=self.model, select_fields=list(self.select_fields))
        compiler.annotation_col_map = {}
        compiler.col_count = len(compiler.select)
        compiler.has_extra_select = False
        compiler.where = compiler.query.where
        compiler.having = compiler.qualify = None


class SelectTemplates:
    def __init__(self, active=lambda: True):
        self.active = active
        self.templates = {}
        self.counts = Counter()
        self.fallbacks = Counter()

    def key(self, compiler, with_limits, with_col_aliases):
        q = compiler.query
        if type(compiler) is not PgCompiler or type(q) is not Query or type(compiler.connection) is not DatabaseWrapper:
            return None, 'compiler-or-backend'
        if not with_limits or with_col_aliases or not compiler.elide_empty:
            return None, 'compiler-mode'
        if getattr(compiler.compile, '__func__', None) is not COMPILE or getattr(compiler.get_select, '__func__', None) is not GET_SELECT:
            return None, 'compiler-hook'
        if set(q.__dict__) - QUERY_KEYS:
            return None, 'unrecognized-query-state'
        if (not q.default_cols or q.select or q.selected or q.values_select or q.annotations or q.extra
                or q.extra_tables or q._filtered_relations or q.external_aliases or q.select_related
                or q.select_for_update or q.distinct or q.distinct_fields or q.group_by is not None
                or q.combinator or q.combined_queries or q.subquery or not q.alias_cols
                or q.order_by or q.extra_order_by or q.default_ordering
                or q.low_mark != 0 or q.high_mark != 21 or q.model._meta.parents):
            return None, 'query-shape'
        where = q.where
        if type(where) is not WhereNode or where.connector != 'AND' or where.negated or len(where.children) != 1:
            return None, 'predicate-shape'
        lookup = where.children[0]
        pk = q.model._meta.pk
        if (type(lookup) is not IntegerFieldExact or type(lookup.lhs) is not Col or type(lookup.rhs) is not int
                or type(pk) not in (AutoField, BigAutoField) or lookup.lhs.target is not pk
                or lookup.lhs.output_field is not pk or pk.get_lookup('exact') is not IntegerFieldExact):
            return None, 'lookup-or-value'
        table = q.model._meta.db_table
        if (list(q.alias_map) != [table] or lookup.lhs.alias != table
                or type(q.alias_map[table]) is not BaseTable or q.alias_map[table].table_name != table
                or q.alias_map[table].table_alias != table or q.alias_refcount != {table: 1}):
            return None, 'table-or-alias'
        fields = q.model._meta.concrete_fields
        names = {f.name for f in fields}
        deferred, defer = q.deferred_loading
        if not set(deferred) <= names | {'pk'}:
            return None, 'related-projection'
        def plain_projection(f):
            method = getattr(f.get_col, '__func__', None)
            return (getattr(f.select_format, '__func__', None) is FIELD_FORMAT and
                    (method is FIELD_COL or
                     (type(f) in (ForeignKey, OneToOneField) and method is FOREIGN_COL
                      and type(f.target_field) in (AutoField, BigAutoField))))
        if not all(plain_projection(f) for f in fields):
            return None, 'projection-hook'
        # No parameter values in the key or the retained template. The connection
        # is scoped to the experiment; schema/code/registry changes outside the
        # explicit guards are not claimed supported by this prototype.
        return (id(compiler.connection), q.model, table,
                tuple((f, f.name, f.attname, f.column, f.target_field if isinstance(f, ForeignKey) else f) for f in fields),
                (tuple(sorted(deferred)), defer), compiler.using), None

    def as_sql(self, compiler, with_limits=True, with_col_aliases=False):
        key, reason = self.key(compiler, with_limits, with_col_aliases)
        if reason:
            self.fallbacks[reason] += 1
            return ORIGINAL(compiler, with_limits=with_limits, with_col_aliases=with_col_aliases)
        template = self.templates.get(key)
        if template is not None:
            self.counts['bind_attempts'] += 1
            template.hydrate(compiler)
            # Reuse the ORIGINAL WHERE pipeline, including integer overflow and
            # DB adaptation. EmptyResultSet propagates to original execute_sql.
            where_sql, params = compiler.compile(compiler.query.where)
            if where_sql != template.where_sql or len(params) != 1:
                # No SQL has executed. Fail closed rather than invent a result,
                # silently reuse an incompatible shape, or duplicate effectful prep.
                raise RuntimeError('Frozen SELECT template contract changed')
            self.counts['hits'] += 1
            return template.sql, tuple(params)

        # Capture the already executed WHERE compilation; do not recompile it
        # or duplicate dynamic field preparation on a cold call.
        captured = []
        original_bound = compiler.compile
        def capture(this, node):
            result = original_bound(node)
            if node is compiler.query.where:
                captured.append(result)
            return result
        compiler.compile = MethodType(capture, compiler)
        try:
            sql, params = ORIGINAL(compiler, with_limits=with_limits, with_col_aliases=with_col_aliases)
        finally:
            del compiler.compile
        self.counts['cold_compilations'] += 1
        assert len(captured) == 1 and len(params) == 1
        assert tuple(captured[0][1]) == params
        assert compiler.klass_info == {'model': compiler.query.model, 'select_fields': list(range(len(compiler.select)))}
        assert compiler.annotation_col_map == {}
        assert compiler.has_extra_select is False
        assert all(type(s[0]) is Col and not s[1][1] and
                   s[0].output_field is (s[0].target.target_field if isinstance(s[0].target, ForeignKey) else s[0].target)
                   for s in compiler.select)
        self.templates[key] = Template(sql, captured[0][0],
            tuple((s[0].alias, s[0].target, s[0].output_field, s[1][0], s[2]) for s in compiler.select),
            compiler.query.model, tuple(compiler.klass_info['select_fields']))
        return sql, params

    def summary(self):
        return dict(counts=dict(self.counts), fallbacks=dict(self.fallbacks), templates=len(self.templates),
                    retained_values='SQL, predicate shape, field/model metadata only; no query, RHS value, row, or model instance')


def dispatch(compiler, with_limits=True, with_col_aliases=False):
    state = ACTIVE.get()
    if state is None or not state.active():
        return ORIGINAL(compiler, with_limits=with_limits, with_col_aliases=with_col_aliases)
    return state.as_sql(compiler, with_limits, with_col_aliases)


@contextmanager
def installed(active=lambda: True):
    # Serialize explicit experiment contexts; unrelated threads lack the
    # ContextVar and retain original execution. No concurrent speedup claim.
    with INSTALL_LOCK:
        if ACTIVE.get() is not None or SQLCompiler.as_sql is not ORIGINAL:
            raise RuntimeError('SELECT experiment requires an unmodified compiler context')
        state = SelectTemplates(active)
        token = ACTIVE.set(state)
        SQLCompiler.as_sql = dispatch
        try:
            yield state
        finally:
            SQLCompiler.as_sql = ORIGINAL
            ACTIVE.reset(token)
