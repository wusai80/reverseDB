"""Real-DB contract experiment for relationalizing an original scalar consumer.

This does NOT fuse COUNT/SUM/UPDATE. Original UPDATE arithmetic, error handler,
and miss/CREATE continuation execute unchanged. Two unsafe alternatives are
negative controls, never installed in the application.
"""
import json
import logging
import os
from pathlib import Path
import sys
from unittest.mock import patch

import psycopg2
import baseline_probe as base

OUT = Path(sys.argv[2]); ARM = os.environ['RDB_ANALYTICS_ARM']
BINDING = json.loads(Path(os.environ['RDB_ANALYTICS_BINDING']).read_text())
READ = BINDING['read_sql']
AGGREGATE = 'SELECT COALESCE(SUM(r."quantity"::numeric), 0) FROM (' + READ + ') AS r'
NARROW = 'SELECT COALESCE(SUM("quantity"::numeric),0) FROM "order_line" WHERE "order_id"=%s'
COMBINED = 'SELECT COUNT(*) AS "__count", COALESCE(SUM(r."quantity"::numeric),0) FROM (' + READ + ') AS r'


def connect(user=None, password=None):
    return psycopg2.connect(host=os.environ['DATABASE_HOST'], port=os.environ['DATABASE_PORT'],
        dbname=os.environ['DATABASE_NAME'], user=user or os.environ['DATABASE_USER'],
        password=password or os.environ['DATABASE_PASSWORD'])


def main():
    import django
    django.setup()
    from django.db import connection, transaction
    from django.db.models import Model, Field, ForeignKey
    from django.db.models.signals import pre_init, post_init, pre_save, post_save
    from oscar.core.loading import get_model
    from oscar.apps.analytics.receivers import _record_user_order
    Order=get_model('order','Order'); Line=get_model('order','Line'); UserRecord=get_model('analytics','UserRecord')
    User=get_model('auth','User')
    original_items=Order.num_items.fget; original_lines=Order.num_lines.fget
    checks=[]; results=[]
    def check(label, ok):
        assert ok, label
        checks.append(label)
    check('original receiver / property code bindings', base.code_hash(_record_user_order.__code__)==BINDING['receiver_hash']
          and base.code_hash(original_items.__code__)==BINDING['items_hash'])
    initial=base.snapshot(connection)
    check('complete committed baseline state restored',initial==json.loads(Path(os.environ['RDB_EXPECTED_STATE']).read_text()))
    with connection.cursor() as c:
        c.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('order_line'::regclass,'analytics_userrecord'::regclass) ORDER BY relname")
        security=c.fetchall()
        c.execute("SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        role=c.fetchone()
        c.execute('SHOW transaction_isolation'); isolation=c.fetchone()[0]
        c.execute("SELECT tgname,tgisinternal FROM pg_trigger WHERE tgrelid IN ('order_line'::regclass,'analytics_userrecord'::regclass)")
        triggers=c.fetchall()
    check('frozen baseline has no RLS or user triggers', all(not a and not b for _,a,b in security) and all(internal for _,internal in triggers))
    check('non-superuser original read committed',not role[1] and not role[2] and isolation=='read committed')
    base.save('binding.json',dict(security=security,role=role,isolation=isolation,aggregate_sql=AGGREGATE,
        scope='Only first num_items read may be relationalized; original count/update/miss branch remain on host'))
    external=connect();external.autocommit=True
    class LogCapture(logging.Handler):
        def emit(self,record):logs.append(dict(level=record.levelname,message=record.getMessage()))
    logger=logging.getLogger('oscar.analytics'); handler=LogCapture();logger.addHandler(handler)
    cases=['ordinary','empty','bag','wide-sum','existing-overflow','missing','missing-wide',
           'miss-mutates-lines','read-interleaving','prefetched-lines','init-mutates','init-fails','update-check-fails']
    if ARM=='naive':cases=['miss-mutates-lines','read-interleaving']
    try:
        for label in cases:
            logs=[]; events=[]; statements=[]; summaries=[]; stats=dict(items_calls=0,aggregates=0,fallbacks=[])
            saved_items=None; cached_pair=None; interleaved=False; error=None; rollback_flag=None
            original_quantities={r['id']:r['quantity'] for r in initial['tables']['order_line'] if r['order_id']==3}
            def guard(order):
                if 'lines' in getattr(order,'_prefetched_objects_cache',{}):return 'prefetched-model-instances'
                if pre_init.has_listeners(Line) or post_init.has_listeners(Line):return 'model-init-effects'
                if Line.__init__ is not Model.__init__ or Line.from_db.__func__ is not Model.from_db.__func__:return 'custom-materialization'
                # Frozen Django ForeignKey override adds an empty-string converter
                # only on backends interpreting empty strings as NULL. PostgreSQL
                # does not; inspect actual field AND backend conversion lists.
                for f in Line._meta.concrete_fields:
                    if (getattr(f.get_db_converters,'__func__',None) not in (Field.get_db_converters,ForeignKey.get_db_converters)
                        or hasattr(f,'from_db_value') or f.get_db_converters(connection)
                        or connection.ops.get_db_converters(f.get_col(Line._meta.db_table))):
                        return 'custom-field-conversion'
                return None
            def items(order):
                nonlocal saved_items
                stats['items_calls']+=1
                if ARM=='naive' and label=='read-interleaving':
                    return cached_pair[1]
                if ARM=='naive' and label=='miss-mutates-lines' and saved_items is not None:
                    return saved_items
                if ARM=='original' or stats['items_calls']>1:
                    return original_items(order)
                reason=guard(order)
                if reason:
                    stats['fallbacks'].append(reason);return original_items(order)
                with connection.cursor() as c:
                    c.execute(AGGREGATE,[order.pk]); numeric=c.fetchone()[0]
                value=int(numeric)
                assert numeric==value
                stats['aggregates']+=1;saved_items=value
                summaries.append(dict(aggregate_type=type(numeric).__name__,host_value=value,host_type=type(value).__name__))
                return value
            def lines(order):
                nonlocal cached_pair
                if ARM=='naive' and label=='read-interleaving':
                    with connection.cursor() as c:
                        c.execute(COMBINED,[order.pk]);a,b=c.fetchone()
                    cached_pair=(int(a),int(b));return cached_pair[0]
                return original_lines(order)
            def observe(execute,sql,params,many,context):
                nonlocal interleaved
                statements.append(dict(sql=sql,params=base.encode(params)))
                result=execute(sql,params,many,context)
                if label=='read-interleaving' and not interleaved and sql.startswith('SELECT COUNT(*) AS "__count"'):
                    with external.cursor() as c:
                        c.execute('UPDATE order_line SET quantity=quantity+1 WHERE order_id=3')
                    interleaved=True
                return result
            def user_init(sender,instance,**kwargs):
                events.append(dict(kind='user_post_init',items=instance.num_order_items,lines=instance.num_order_lines))
            def user_pre(sender,instance,**kwargs):
                events.append(dict(kind='user_pre_save',items=instance.num_order_items,lines=instance.num_order_lines))
            def user_post(sender,instance,created,**kwargs):
                events.append(dict(kind='user_post_save',created=created,items=instance.num_order_items,lines=instance.num_order_lines))
            def line_effect(sender,instance,**kwargs):
                events.append(dict(kind='line_post_init',id=instance.pk,quantity=instance.quantity))
                if label=='init-fails':raise RuntimeError('original Line initialization failure')
                instance.quantity+=7
            with transaction.atomic():
                with connection.cursor() as c:
                    c.execute('UPDATE analytics_userrecord SET num_orders=0,num_order_lines=0,num_order_items=0,total_spent=0 WHERE user_id=1')
                    if label in ['bag','wide-sum','missing-wide']:
                        ids=sorted(original_quantities)[:2]
                        c.execute('UPDATE order_line SET quantity=0 WHERE order_id=3')
                        c.execute('UPDATE order_line SET quantity=%s,product_id=1 WHERE id=ANY(%s)',[2 if label=='bag' else 2**31-1,ids])
                    if label=='existing-overflow':
                        c.execute('UPDATE analytics_userrecord SET num_order_items=2147483647 WHERE user_id=1')
                    if label in ['missing','missing-wide','miss-mutates-lines']:
                        c.execute('DELETE FROM analytics_userrecord WHERE user_id=1')
                    if label=='empty':
                        columns=[f.column for f in Order._meta.concrete_fields]; q=connection.ops.quote_name
                        params=[];expressions=[]
                        for col in columns:
                            if col in ['id','number','basket_id']:
                                expressions.append('%s');params.append({'id':900001,'number':'RDB-CONTRACT-EMPTY','basket_id':None}[col])
                            else:expressions.append(q(col))
                        c.execute('INSERT INTO order_order ('+', '.join(map(q,columns))+') SELECT '+', '.join(expressions)+' FROM order_order WHERE id=3',params)
                    if label=='miss-mutates-lines':
                        c.execute("CREATE FUNCTION reversedb_contract_trigger() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE order_line SET quantity=quantity+1 WHERE order_id=3; RETURN NULL; END $$")
                        c.execute('CREATE TRIGGER contract_miss AFTER UPDATE ON analytics_userrecord FOR EACH STATEMENT EXECUTE FUNCTION reversedb_contract_trigger()')
                    if label=='update-check-fails':
                        c.execute("CREATE FUNCTION reversedb_contract_trigger() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic update check' USING ERRCODE='23514'; END $$")
                        c.execute('CREATE TRIGGER contract_error BEFORE UPDATE ON analytics_userrecord FOR EACH ROW EXECUTE FUNCTION reversedb_contract_trigger()')
                user=User.objects.get(pk=1)
                order=Order.objects.get(pk=900001 if label=='empty' else 3)
                alias=None
                if label=='prefetched-lines':
                    order=Order.objects.prefetch_related('lines').get(pk=3)
                    alias=order.lines.all()[0];alias.quantity=9
                signals=[(post_init,user_init,UserRecord),(pre_save,user_pre,UserRecord),(post_save,user_post,UserRecord)]
                if label in ['init-mutates','init-fails']:signals.append((post_init,line_effect,Line))
                for signal,fn,sender in signals:signal.connect(fn,sender=sender,weak=False)
                try:
                    with patch.object(Order,'num_items',property(items)),patch.object(Order,'num_lines',property(lines)),connection.execute_wrapper(observe):
                        try:
                            with transaction.atomic():
                                _record_user_order(user,order)
                                rollback_flag=connection.needs_rollback
                        except Exception as exc:
                            cause=exc.__cause__
                            error=dict(type=type(exc).__qualname__,message=str(exc),sqlstate=getattr(cause,'pgcode',None))
                finally:
                    for signal,fn,sender in signals:signal.disconnect(fn,sender=sender)
                same_alias=alias is None or order.lines.all()[0] is alias
                state=base.snapshot(connection);base.save(label+'-full-state.json',state)
                target=[r for r in state['tables']['analytics_userrecord'] if r['user_id']==1]
                results.append(dict(case=label,error=error,rollback_flag=rollback_flag,logs=list(logs),model_events=events,
                                    user_records=target,prefetched_alias_preserved=same_alias,interleaved=interleaved,
                                    stats=stats,aggregate_values=summaries,sql=statements))
                base.save('cases.json',results)
                transaction.set_rollback(True)
            if interleaved:
                with external.cursor() as c:
                    c.executemany('UPDATE order_line SET quantity=%s WHERE id=%s',[(qty,pk) for pk,qty in original_quantities.items()])
            # Sequence advancement is observed and compared above, then restored
            # explicitly; transaction rollback alone never restores sequences.
            with connection.cursor() as c:
                for seq,(value,called) in initial['sequences'].items():c.execute('SELECT setval(%s::regclass,%s,%s)',[seq,value,called])
            check(label+' full data and sequences restored',base.snapshot(connection)==initial)
            if ARM=='candidate':
                if label in ['init-mutates','init-fails','prefetched-lines']:
                    check(label+' original effectful path retained',stats['aggregates']==0 and bool(stats['fallbacks']))
                else:check(label+' actual aggregate query executed',stats['aggregates']==1)
        base.save('cases.json',results)

        # Permission counterexample: narrowing the projection silently broadens
        # access for a column-restricted caller. Keep original projected columns.
        acl=[]
        with connection.cursor() as c:
            c.execute('GRANT SELECT (order_id,quantity) ON order_line TO oscar_projection_reader')
        reader=connect('oscar_projection_reader','synthetic-projection-only');reader.autocommit=True
        try:
            for label,sql in [('original-full-row',READ),('naive-quantity-only',NARROW),('full-projection-aggregate',AGGREGATE)]:
                try:
                    with reader.cursor() as c:c.execute(sql,[3]);value=c.fetchall()
                    acl.append(dict(case=label,value=base.encode(value),error=None))
                except Exception as exc:acl.append(dict(case=label,error=dict(type=type(exc).__name__,message=str(exc),sqlstate=getattr(exc,'pgcode',None))))
            check('column permissions retained, naive projection rejected',acl[0]['error']['sqlstate']==acl[2]['error']['sqlstate']=='42501' and acl[1]['error'] is None)
        finally:reader.close()
        base.save('permissions.json',acl)
        check('all data intact after all experiments',base.snapshot(connection)==initial)
        base.save('result.json',dict(status='passed',checks=checks,arm=ARM,cases=len(results),
            limits=['Aggregation stage only; count and UPDATE remain separate original executions',
                    'No 3-to-1 backend, generated fusion, performance or general admission claim',
                    'Interleaving uses committed existing orders; original new-order transaction ownership is not inferred',
                    'Frozen converters/managers/schema; absent RLS verified, not generalized to arbitrary policies']))
    finally:
        logger.removeHandler(handler);external.close();connection.close()


if __name__=='__main__':main()
