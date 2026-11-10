"""Experimental source-bound SQL result handoff; never installed by the application.

Both original QuerySet/Model pipelines run. Only the two compiler execution
boundaries are joined, in this explicitly enabled, fixed-runtime experiment.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import sys
from threading import RLock

PACKET_SQL = '''CREATE FUNCTION public.reversedb_allocate_packet(p_quantity integer, p_id bigint)
RETURNS TABLE(affected integer, id bigint, num_allocated integer)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, public AS $body$
DECLARE changed integer; selected_id bigint; selected_allocated integer;
BEGIN
    UPDATE public.partner_stockrecord AS s
       SET num_allocated=COALESCE(s.num_allocated,0)+p_quantity WHERE s.id=p_id;
    GET DIAGNOSTICS changed = ROW_COUNT;
    SELECT s.id,s.num_allocated INTO selected_id,selected_allocated
      FROM public.partner_stockrecord AS s WHERE s.id=p_id LIMIT 21;
    RETURN QUERY SELECT changed,selected_id,selected_allocated;
END
$body$'''
CALL = 'SELECT * FROM public.reversedb_allocate_packet(%s,%s)'
STATE = ContextVar('oscar_experimental_handoff', default=None)
INSTALL_LOCK = RLock()
ACTIVE_INSTALL = None


@contextmanager
def installed(binding):
    """Local experiment only. Scope/frozen sources are checked by the runner.

    Custom managers, routers, instrumentation wrappers, SQL mismatches, wide
    Python integers and non-atomic use retain the original execution path.
    No exceptions from an executed candidate are caught and retried.
    """
    global ACTIVE_INSTALL
    from django.conf import settings
    from django.db import models
    from django.db.models.sql.compiler import SQLCompiler, SQLUpdateCompiler
    from django.db.backends.postgresql.compiler import SQLCompiler as PostgreSQLCompiler
    from django.db.models.sql.constants import MULTI, ROW_COUNT
    from oscar.core.loading import get_model
    Stock = get_model('partner', 'StockRecord')
    code = Stock.allocate.__code__
    # Global methods are installed once; each context has its own packet/state.
    # Never stack wrappers from overlapping threads and restore them out of order.
    with INSTALL_LOCK:
        original_select = ACTIVE_INSTALL['original_select'] if ACTIVE_INSTALL else SQLCompiler.execute_sql
        original_update = ACTIVE_INSTALL['original_update'] if ACTIVE_INSTALL else SQLUpdateCompiler.execute_sql
    stats = dict(fused_updates=0, reused_reads=0, fallbacks=[], leftover_results=0)
    state = dict(pending=None, stats=stats)

    def allocation_frame(offset):
        frame = sys._getframe(1)
        while frame:
            if frame.f_code is code:
                return frame if frame.f_lasti == offset else None
            frame = frame.f_back
        return None

    def update(compiler, result_type):
        state = STATE.get()
        frame = allocation_frame(binding['update_offset'])
        if state is None or frame is None or compiler.query.model is not Stock:
            return original_update(compiler, result_type)
        reason = None
        if result_type != ROW_COUNT or type(compiler) is not SQLUpdateCompiler:
            reason = 'unsupported compiler/result type'
        elif not compiler.connection.in_atomic_block or compiler.using != 'default':
            reason = 'caller transaction/alias not admitted'
        elif settings.DATABASE_ROUTERS or compiler.connection.execute_wrappers:
            reason = 'custom router or execute wrapper'
        elif type(Stock._base_manager) is not models.Manager or type(Stock._default_manager) is not models.Manager:
            reason = 'custom manager'
        elif Stock.refresh_from_db is not models.Model.refresh_from_db or Stock.from_db.__func__ is not models.Model.from_db.__func__:
            reason = 'custom model materializer/refresh'
        elif compiler.query.related_updates:
            reason = 'related updates'
        if reason:
            state['stats']['fallbacks'].append(reason)
            return original_update(compiler, result_type)
        statement, params = compiler.as_sql()
        if (statement != binding['update_sql'] or len(params) != 3 or params[0] != 0
            or type(params[1]) is not int or not -2147483648 <= params[1] <= 2147483647
            or type(params[2]) is not int or not -9223372036854775808 <= params[2] <= 9223372036854775807):
            state['stats']['fallbacks'].append('SQL or parameter ABI mismatch')
            return original_update(compiler, result_type)
        if state['pending'] is not None:
            raise AssertionError('Unconsumed handoff before a new update')
        with compiler.connection.cursor() as cursor:
            cursor.execute(CALL, (params[1], params[2]))
            packet = cursor.fetchall()
        assert len(packet) == 1 and len(packet[0]) == 3
        affected, pk, allocated = packet[0]
        assert affected in (0, 1)
        rows = [] if pk is None else [(pk, allocated)]
        state['pending'] = dict(frame=frame, connection=compiler.connection, pk=params[2], rows=rows)
        state['stats']['fused_updates'] += 1
        # Preserve QuerySet.update()'s own row-count result, even for a skipped update.
        return affected

    def select(compiler, result_type=MULTI, chunked_fetch=False, chunk_size=100):
        state = STATE.get()
        pending = state and state['pending']
        frame = allocation_frame(binding['read_offset'])
        if pending and frame is pending['frame'] and compiler.connection is pending['connection']:
            statement, params = compiler.as_sql()
            if (type(compiler) is PostgreSQLCompiler and result_type == MULTI and not chunked_fetch
                and statement == binding['read_sql'] and tuple(params) == (pending['pk'],)):
                state['pending'] = None
                state['stats']['reused_reads'] += 1
                # Same row-block shape as original non-chunked execute_sql(MULTI).
                # results_iter(), field conversion, from_db(), get(), and
                # refresh_from_db() continue untouched, including zero-row errors.
                return [pending['rows']] if pending['rows'] else []
            # A mismatched read is never served stale rows; fail closed in this
            # experiment instead of silently claiming a verified candidate.
            raise AssertionError('Original refresh SQL/ABI changed after fused update')
        return original_select(compiler, result_type, chunked_fetch, chunk_size)

    with INSTALL_LOCK:
        if ACTIVE_INSTALL:
            if ACTIVE_INSTALL['binding'] != binding or ACTIVE_INSTALL['code'] is not code:
                raise RuntimeError('Overlapping handoff requires the same source binding')
            if SQLUpdateCompiler.execute_sql is not ACTIVE_INSTALL['update'] or SQLCompiler.execute_sql is not ACTIVE_INSTALL['select']:
                raise RuntimeError('Compiler hooks changed during handoff installation')
            ACTIVE_INSTALL['users'] += 1
        else:
            SQLUpdateCompiler.execute_sql = update
            SQLCompiler.execute_sql = select
            ACTIVE_INSTALL = dict(binding=dict(binding), code=code, users=1,
                                  original_select=original_select, original_update=original_update,
                                  update=update, select=select)
    token = STATE.set(state)
    try:
        yield stats
    finally:
        if state['pending'] is not None:
            stats['leftover_results'] += 1
        state['pending'] = None
        STATE.reset(token)
        with INSTALL_LOCK:
            ACTIVE_INSTALL['users'] -= 1
            if ACTIVE_INSTALL['users'] == 0:
                SQLUpdateCompiler.execute_sql = ACTIVE_INSTALL['original_update']
                SQLCompiler.execute_sql = ACTIVE_INSTALL['original_select']
                ACTIVE_INSTALL = None
