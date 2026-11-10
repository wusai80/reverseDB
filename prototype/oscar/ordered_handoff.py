"""Experiment-scoped first ordered read pair, bound to frozen receiver sources.

Preserves the original receiver and miss continuation. This is not a generally
admitted backend: permission diagnostics and arbitrary host hooks remain open.
"""
from contextlib import contextmanager
import dis
import sys
from unittest.mock import patch


@contextmanager
def installed(binding, code_hash):
    from django.db import connection
    from django.db.models import Model, Field, ForeignKey, F
    from django.db.models import expressions
    from django.db.models.signals import pre_init, post_init
    from oscar.core.loading import get_model
    from oscar.apps.analytics import receivers
    Order=get_model('order','Order');Line=get_model('order','Line')
    receiver=receivers._record_user_order
    old_lines=Order.num_lines;old_items=Order.num_items
    assert code_hash(receiver.__code__)==binding['receiver_hash']
    assert code_hash(old_items.fget.__code__)==binding['items_hash']
    instructions=list(dis.get_instructions(receiver))
    def first_site(name):
        sites=[(x.offset,instructions[n+1].offset) for n,x in enumerate(instructions) if x.opname=='LOAD_ATTR' and x.argval==name]
        assert len(sites)==2
        return sites[0]
    line_site=first_site('num_lines');item_site=first_site('num_items')
    members=[(F,'__init__'),(expressions.Combinable,'__add__'),(expressions.Combinable,'_combine'),
             (expressions.CombinedExpression,'__init__'),(expressions.Value,'__init__'),(expressions.Expression,'__init__')]
    frozen=[(cls,name,getattr(cls,name)) for cls,name in members]
    # Keep the already-tested function bodies unchanged. The negative-control
    # and original alternatives are unavailable to users of this context.
    arm='guarded';pending=None
    stats=dict(packets=0,consumed=0,miss_lines=0,miss_items=0,fallbacks=[],values=[],
               line_site=line_site,item_site=item_site,
               packet_sql='SELECT * FROM public.reversedb_ordered_summary(%s)')

    def guard(order):
        if receivers.F is not F or any(getattr(cls,name) is not fn for cls,name,fn in frozen):return 'host-expression-effects'
        if connection.execute_wrappers:return 'query-wrapper-effects'
        if 'lines' in getattr(order,'_prefetched_objects_cache',{}):return 'prefetched-model-instances'
        if pre_init.has_listeners(Line) or post_init.has_listeners(Line):return 'model-init-effects'
        if Line.__init__ is not Model.__init__ or Line.from_db.__func__ is not Model.from_db.__func__:return 'custom-materialization'
        for f in Line._meta.concrete_fields:
            if (getattr(f.get_db_converters,'__func__',None) not in (Field.get_db_converters,ForeignKey.get_db_converters)
                or hasattr(f,'from_db_value') or f.get_db_converters(connection)
                or connection.ops.get_db_converters(f.get_col(Line._meta.db_table))):return 'custom-field-conversion'
        return None

    def lines(order):
        nonlocal pending
        frame=sys._getframe(1)
        selected=frame.f_code is receiver.__code__ and line_site[0]<=frame.f_lasti<line_site[1]
        if not selected:
            stats['miss_lines']+=1
            return old_lines.fget(order)
        if arm=='original':return old_lines.fget(order)
        reason=guard(order) if arm=='guarded' else None
        if reason:
            stats['fallbacks'].append(reason)
            return old_lines.fget(order)
        with connection.cursor() as c:
            c.execute('SELECT * FROM public.reversedb_ordered_summary(%s)',[order.pk]);n,q=c.fetchone()
        assert q==int(q)
        # A one-shot handoff owned by this receiver frame and object.
        # Never attach it to an Order or reuse it after UPDATE.
        pending=(frame,order,int(q));stats['packets']+=1;stats['values'].append([n,int(q)])
        return n

    def items(order):
        nonlocal pending
        frame=sys._getframe(1)
        selected=frame.f_code is receiver.__code__ and item_site[0]<=frame.f_lasti<item_site[1]
        if selected and pending is not None:
            owner,obj,value=pending
            assert owner is frame and obj is order
            pending=None;stats['consumed']+=1
            return value
        if not selected:stats['miss_items']+=1
        return old_items.fget(order)

    try:
        with patch.object(Order,'num_lines',property(lines)),patch.object(Order,'num_items',property(items)):
            yield stats
    finally:
        stats['pending_at_exit']=pending is not None
        pending=None
        assert Order.num_lines is old_lines and Order.num_items is old_items
