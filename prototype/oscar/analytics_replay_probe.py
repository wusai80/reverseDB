"""Fixed full-order replay and source-bound intermediate-object observations."""
from collections import Counter
from contextlib import nullcontext
import json
import os
from pathlib import Path
from unittest.mock import patch
import baseline_probe as base


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=base.TraceCursor,connection_factory=base.TraceConnection)
    django.setup()
    from django.db import connection
    from django.db.models import Model
    from oscar.core.loading import get_model
    from oscar.apps.analytics.receivers import _record_user_order
    from analytics_relationalization import installed,first_items_site
    Order=get_model('order','Order');Line=get_model('order','Line')
    original_items=Order.num_items
    binding=json.loads(Path(os.environ['RDB_ANALYTICS_BINDING']).read_text())
    enabled=os.environ['RDB_ANALYTICS_ARM']=='candidate'
    checks=[]
    def check(name,ok):
        assert ok,name
        checks.append(name)
    check('original source-bound receiver and property',base.code_hash(_record_user_order.__code__)==binding['receiver_hash']
          and base.code_hash(original_items.fget.__code__)==binding['items_hash'])
    with connection.cursor() as c:
        c.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('order_line'::regclass,'analytics_userrecord'::regclass)")
        policies=c.fetchall()
        c.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('order_line'::regclass,'analytics_userrecord'::regclass)")
        triggers=c.fetchall()
        c.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        role=c.fetchone()
        c.execute('SHOW transaction_isolation');isolation=c.fetchone()[0]
    check('frozen original database security configuration',len(policies)==2 and all(not a and not b for a,b in policies)
          and not triggers and role==(False,False) and isolation=='read committed')
    connection.close()
    start,stop=first_items_site(_record_user_order)
    counts=Counter();by_model=Counter();removed=[];old_profile=base.profile
    def profile(frame,event,arg):
        before=len(base.EVENTS)
        old_profile(frame,event,arg)
        if event=='call' and frame.f_code in (Model.__init__.__code__,Model.from_db.__func__.__code__):
            kind='init' if frame.f_code is Model.__init__.__code__ else 'from_db'
            obj=frame.f_locals.get('self',frame.f_locals.get('cls'))
            counts[kind]+=1;by_model[kind+':'+obj._meta.label]+=1
        if frame.f_code is not Model.from_db.__func__.__code__ or event not in ('call','return'):return
        parent=frame.f_back
        while parent and parent.f_code is not _record_user_order.__code__:parent=parent.f_back
        selected=parent is not None and start<=parent.f_lasti<stop
        order_id=parent.f_locals['order'].pk if selected else None
        del parent
        if selected:
            assert frame.f_locals['cls'] is Line
            # Mark only original Model.from_db events under the selected scalar
            # consumer. Every other Python event must remain in the comparison.
            for e in base.EVENTS[before:]:e['initial_items_materialization']=True
            if event=='return':
                removed.append(dict(order_id=order_id,identity=base.identity(arg),
                                    fields=base.encode({f.attname:getattr(arg,f.attname) for f in Line._meta.concrete_fields})))
    original_save=base.save
    def save(name,value):
        original_save(name,value)
        if name in {n+'-trace.json' for n in ['RDB-ONE','RDB-THREE','RDB-SIX','RDB-DIGITAL']}:
            # baseline saves the trace after leaving the actual atomic scope;
            # capture each committed state without adding calls to its trace.
            original_save(name.replace('-trace','-committed-state'),base.snapshot(connection))
    with (installed(binding,base.code_hash) if enabled else nullcontext()) as stats:
        with patch.object(base,'profile',profile),patch.object(base,'save',save):base.main()
    check('property restored',Order.num_items is original_items)
    if enabled:
        check('four intended first reads actually relationalized',len(stats['reads'])==4 and
              [r['order_id'] for r in stats['reads']]==[1,2,3,4] and all(r['fallback'] is None for r in stats['reads']))
        check('no first-read Line objects constructed',not removed)
    else:check('eleven original first-read Line objects observed',len(removed)==11)
    base.save('relationalization.json',dict(arm=os.environ['RDB_ANALYTICS_ARM'],counts=dict(counts),
              counts_by_model=dict(by_model),initial_materializations=removed,specialization=stats,
              checks=checks,scope='Original direct OrderCreator plus actual commit, not full HTTP checkout',performance_claim=None))


if __name__=='__main__':main()
