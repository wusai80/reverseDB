"""Experiment-only first-read specialization, bound to original receiver bytecode.

No result cache, receiver rewrite, statement fusion, or global application install.
Runner freezes sources/config/schema. This is not a general admission mechanism.
"""
from contextlib import contextmanager
import dis
import sys
from unittest.mock import patch


def first_items_site(receiver):
    instructions=list(dis.get_instructions(receiver))
    sites=[(i.offset,instructions[n+1].offset) for n,i in enumerate(instructions)
           if i.opname=='LOAD_ATTR' and i.argval=='num_items']
    assert len(sites)==2, 'Original update / miss-read structure changed'
    return sites[0]


@contextmanager
def installed(binding, code_hash):
    from django.db import connection
    from django.db.models import Model, Field, ForeignKey
    from django.db.models.signals import pre_init, post_init
    from oscar.core.loading import get_model
    from oscar.apps.analytics.receivers import _record_user_order
    Order=get_model('order','Order');Line=get_model('order','Line')
    original_property=Order.num_items;original_items=original_property.fget
    assert code_hash(_record_user_order.__code__)==binding['receiver_hash']
    assert code_hash(original_items.__code__)==binding['items_hash']
    start,stop=first_items_site(_record_user_order)
    AGGREGATE='SELECT COALESCE(SUM(r."quantity"::numeric), 0) FROM ('+binding['read_sql']+') AS r'
    stats=dict(site=[start,stop],aggregate_sql=AGGREGATE,reads=[],passthrough=0)

    def guard(order):
        if 'lines' in getattr(order,'_prefetched_objects_cache',{}):return 'prefetched-model-instances'
        if pre_init.has_listeners(Line) or post_init.has_listeners(Line):return 'model-init-effects'
        if Line.__init__ is not Model.__init__ or Line.from_db.__func__ is not Model.from_db.__func__:return 'custom-materialization'
        # Same frozen guard as the passing local contract, including ForeignKey.
        for f in Line._meta.concrete_fields:
            if (getattr(f.get_db_converters,'__func__',None) not in (Field.get_db_converters,ForeignKey.get_db_converters)
                or hasattr(f,'from_db_value') or f.get_db_converters(connection)
                or connection.ops.get_db_converters(f.get_col(Line._meta.db_table))):
                return 'custom-field-conversion'
        return None

    def items(order):
        frame=sys._getframe(1)
        selected=(frame.f_code is _record_user_order.__code__ and start<=frame.f_lasti<stop)
        offset=frame.f_lasti
        del frame
        if not selected:
            stats['passthrough']+=1
            return original_items(order)
        record=dict(order_id=order.pk,offset=offset,fallback=None)
        stats['reads'].append(record)
        reason=guard(order)
        if reason:
            record['fallback']=reason
            return original_items(order)
        with connection.cursor() as c:
            c.execute(AGGREGATE,[order.pk]); numeric=c.fetchone()[0]
        value=int(numeric)
        assert numeric==value
        record.update(value=value,aggregate_type=type(numeric).__name__,host_type=type(value).__name__)
        return value

    with patch.object(Order,'num_items',property(items)):
        yield stats
    assert Order.num_items is original_property
