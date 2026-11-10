"""Offline experiment: residualize one pure INSERT parameter-assembly subgraph.

This is an explicit, source-bound prototype, not an installed Django patch or an
automatically admitted backend. No SQL, parameter values, rows or models are
cached. Callers must verify the pinned framework/consumer bytecode first.
"""
from collections import Counter
import datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import fields as django_fields
from django.db.models.fields import related
from django.db.backends.postgresql.compiler import SQLInsertCompiler
from django.db.backends.base.operations import BaseDatabaseOperations
from django.db.models.sql.compiler import SQLInsertCompiler as BaseInsertCompiler


SCALARS = frozenset((type(None), bool, int, float, str, bytes, Decimal,
                     datetime.date, datetime.datetime, datetime.time,
                     datetime.timedelta, UUID))
FIELD_TYPES = frozenset(
    obj for module in (django_fields, related) for obj in vars(module).values()
    if isinstance(obj, type) and issubclass(obj, django_fields.Field)
    and obj.__module__ == module.__name__
)
ORIGINAL = SQLInsertCompiler.assemble_as_sql
FIELD_AS_SQL = BaseInsertCompiler.field_as_sql
MODIFY_PARAMS = BaseDatabaseOperations.modify_insert_params


class LiteralInsertAssembler:
    """Under the bound contract, A(F,[v]) = ((('%s',)*len(F)),), [list(v)].

    Guard before skipping any original field operation. The first eligible call
    obtains its immutable shape from the original implementation. Subsequent
    calls bind fresh parameter containers, preserving every value reference.
    The residual is hand-written; this does not claim general partial evaluation.
    """

    def __init__(self):
        self.shapes = {}  # Only column count -> immutable placeholder tuples.
        self.counts = Counter()
        self.fallbacks = Counter()

    def reason(self, compiler, fields, value_rows):
        if type(compiler) is not SQLInsertCompiler:
            return 'compiler-subclass'
        if getattr(compiler.field_as_sql, '__func__', None) is not FIELD_AS_SQL:
            return 'custom-field-compiler'
        if getattr(compiler.connection.ops.modify_insert_params, '__func__', None) is not MODIFY_PARAMS:
            return 'custom-parameter-hook'
        # Exact containers avoid consuming generators or invoking user-defined
        # iteration/length methods before the original implementation does.
        if type(fields) not in (list, tuple) or type(value_rows) is not list:
            return 'custom-container'
        if len(value_rows) != 1:
            return 'not-single-row'
        row = value_rows[0]
        if type(row) is not list or len(row) != len(fields) or not fields:
            return 'row-shape'
        if any(type(field) not in FIELD_TYPES for field in fields):
            return 'raw-or-custom-field'
        if any(hasattr(field, 'get_placeholder') for field in fields):
            return 'field-placeholder'
        if any(type(value) not in SCALARS for value in row):
            return 'expression-or-custom-value'
        return None

    def assemble(self, compiler, fields, value_rows):
        reason = self.reason(compiler, fields, value_rows)
        if reason:
            self.fallbacks[reason] += 1
            return ORIGINAL(compiler, fields, value_rows)
        width = len(fields)
        if width not in self.shapes:
            result = ORIGINAL(compiler, fields, value_rows)
            placeholders, params = result
            # This assertion checks the frozen contract, not a licence inferred
            # from a sample. A contradiction must stop this offline experiment.
            assert placeholders == (('%s',) * width,)
            assert len(params) == 1 and len(params[0]) == width
            assert all(a is b for a, b in zip(params[0], value_rows[0]))
            self.shapes[width] = placeholders
            self.counts['shape_builds'] += 1
            return result
        self.counts['residual_calls'] += 1
        self.counts['field_dispatches_removed'] += width
        return self.shapes[width], [list(value_rows[0])]
