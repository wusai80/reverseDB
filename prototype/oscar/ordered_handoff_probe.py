"""Bounded real-receiver handoff experiment, not an installed optimizer backend.

Three arms exercise the original receiver: original, deliberately eager negative
control, and a frozen-source guarded first-read handoff. No miss-pair reuse.
"""
from contextlib import ExitStack
import dis
import json
import logging
import os
from pathlib import Path
import sys
from unittest.mock import patch
import baseline_probe as base


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS'] = dict(cursor_factory=base.TraceCursor)
    django.setup()
    from django.db import connection, transaction
    from django.db.models import Model, Field, ForeignKey, F
    from django.db.models import expressions
    from django.db.models.signals import pre_init, post_init, pre_save, post_save
    from freezegun import freeze_time
    from oscar.core.loading import get_model
    from oscar.apps.analytics import receivers
    Order=get_model('order','Order');Line=get_model('order','Line')
    User=get_model('auth','User');UserRecord=get_model('analytics','UserRecord')
    binding=json.loads(Path(os.environ['RDB_ANALYTICS_BINDING']).read_text())
    function_evidence=json.loads(Path(os.environ['RDB_ORDERED_FUNCTIONS']).read_text())
    receiver=receivers._record_user_order
    old_lines=Order.num_lines;old_items=Order.num_items
    checks=[];results=[]
    def check(n,v):
        assert v,n
        checks.append(n)
    check('original receiver and items source bound',base.code_hash(receiver.__code__)==binding['receiver_hash']
          and base.code_hash(old_items.fget.__code__)==binding['items_hash'])
    instructions=list(dis.get_instructions(receiver))
    def first_site(name):
        sites=[(x.offset,instructions[n+1].offset) for n,x in enumerate(instructions) if x.opname=='LOAD_ATTR' and x.argval==name]
        assert len(sites)==2
        return sites[0]
    line_site=first_site('num_lines');item_site=first_site('num_items')
    # The code crossed by hoisting is real host computation, not an empty edge.
    gap=[dict(offset=x.offset,op=x.opname,arg=x.argrepr) for x in instructions if line_site[1]<=x.offset<item_site[0]]
    check('host gap contains addition and F construction',any(x['op']=='BINARY_OP' for x in gap) and any(x['op']=='CALL' for x in gap))
    members=[(F,'__init__'),(expressions.Combinable,'__add__'),(expressions.Combinable,'_combine'),
             (expressions.CombinedExpression,'__init__'),(expressions.Value,'__init__'),(expressions.Expression,'__init__')]
    frozen=[(cls,name,getattr(cls,name)) for cls,name in members]
    base.save('host-gap.json',dict(line_site=line_site,item_site=item_site,instructions=gap,
        methods=[dict(owner=cls.__qualname__,name=name,code=base.code_hash(fn.__code__)) for cls,name,fn in frozen],
        scope='Frozen-source local witness, not a complete proof against arbitrary Python mutation'))
    initial=base.snapshot(connection)
    check('full seeded original state',initial==json.loads(Path(os.environ['RDB_EXPECTED_STATE']).read_text()))
    with connection.cursor() as c:
        c.execute(function_evidence['definitions']['reversedb_ordered_summary'])
        c.execute('REVOKE ALL ON FUNCTION public.reversedb_ordered_summary(bigint) FROM PUBLIC')
        c.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        check('ordinary invoker role',c.fetchone()==(False,False))
    cases=['ordinary','missing','miss-mutates-lines','prefetched-lines','init-mutates','init-fails',
           'F-mutates','F-fails','addition-mutates','addition-fails','wrapper-mutates','existing-overflow']
    class Capture(logging.Handler):
        def emit(self,record):logs.append(dict(level=record.levelname,message=record.getMessage()))
    logger=logging.getLogger('oscar.analytics');handler=Capture();logger.addHandler(handler)
    try:
        with freeze_time('2026-11-06 01:00:00+00:00'):
            for label in cases:
                for arm in ['original','eager-negative-control','guarded']:
                    effects=[];logs=[];events=[];error=None;rollback=None;pending=None
                    stats=dict(packets=0,consumed=0,miss_lines=0,miss_items=0,fallbacks=[],values=[])
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
                    def mutate_or_fail(kind):
                        effects.append(kind)
                        if label.endswith('fails'):raise RuntimeError('synthetic host expression failure')
                        with connection.cursor() as c:c.execute('UPDATE order_line SET quantity=quantity+1 WHERE order_id=3')
                    def custom_f(name):
                        if name=='num_order_items':mutate_or_fail('F:num_order_items')
                        return F(name)
                    original_add=expressions.Combinable.__add__
                    def custom_add(obj,other):
                        if isinstance(obj,F) and obj.name=='num_order_lines':mutate_or_fail('addition:num_order_lines')
                        return original_add(obj,other)
                    def line_callback(sender,instance,**kwargs):
                        events.append(dict(kind='line-init',id=instance.pk,quantity=instance.quantity))
                        if label=='init-fails':raise RuntimeError('synthetic Line initialization failure')
                        instance.quantity+=7
                    def user_pre(sender,instance,**kwargs):events.append(dict(kind='user-pre',items=instance.num_order_items))
                    def user_post(sender,instance,created,**kwargs):events.append(dict(kind='user-post',items=instance.num_order_items,created=created))
                    def wrapper(execute,sql,params,many,context):
                        result=execute(sql,params,many,context)
                        if sql==binding['count_sql']:
                            effects.append('after-count-wrapper')
                            with connection.cursor() as c:c.execute('UPDATE order_line SET quantity=quantity+1 WHERE order_id=3')
                        return result
                    with transaction.atomic():
                        with connection.cursor() as c:
                            c.execute('UPDATE analytics_userrecord SET num_orders=0,num_order_lines=0,num_order_items=0,total_spent=0 WHERE user_id=1')
                            if label=='existing-overflow':c.execute('UPDATE analytics_userrecord SET num_order_items=2147483647 WHERE user_id=1')
                            if label in ['missing','miss-mutates-lines']:c.execute('DELETE FROM analytics_userrecord WHERE user_id=1')
                            if label=='miss-mutates-lines':
                                c.execute('CREATE FUNCTION public.reversedb_handoff_miss() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE public.order_line SET quantity=quantity+1 WHERE order_id=3; RETURN NULL; END $$')
                                c.execute('CREATE TRIGGER handoff_miss AFTER UPDATE ON analytics_userrecord FOR EACH STATEMENT EXECUTE FUNCTION public.reversedb_handoff_miss()')
                        user=User.objects.get(pk=1);order=Order.objects.get(pk=3);alias=None
                        if label=='prefetched-lines':
                            order=Order.objects.prefetch_related('lines').get(pk=3);alias=order.lines.all()[0];alias.quantity=9
                        with ExitStack() as stack:
                            stack.enter_context(patch.object(Order,'num_lines',property(lines)))
                            stack.enter_context(patch.object(Order,'num_items',property(items)))
                            if label.startswith('F-'):stack.enter_context(patch.object(receivers,'F',custom_f))
                            if label.startswith('addition-'):stack.enter_context(patch.object(expressions.Combinable,'__add__',custom_add))
                            if label=='wrapper-mutates':stack.enter_context(connection.execute_wrapper(wrapper))
                            signals=[(pre_save,user_pre,UserRecord),(post_save,user_post,UserRecord)]
                            if label.startswith('init-'):signals.append((post_init,line_callback,Line))
                            for signal,fn,sender in signals:
                                signal.connect(fn,sender=sender,weak=False)
                                stack.callback(signal.disconnect,fn,sender=sender)
                            base.EVENTS.clear()
                            with transaction.atomic():
                                base.ACTIVE=True
                                try:
                                    receiver(user,order);rollback=connection.needs_rollback
                                except Exception as exc:
                                    error=dict(type=type(exc).__qualname__,message=str(exc),sqlstate=getattr(exc.__cause__,'pgcode',None))
                                    transaction.set_rollback(True)
                                finally:base.ACTIVE=False
                            sql_events=[{k:v for k,v in e.items() if k!='stack'} for e in base.EVENTS]
                        same_alias=alias is None or order.lines.all()[0] is alias
                        snapshot=base.snapshot(connection);base.save(label+'-'+arm+'-full-state.json',snapshot)
                        results.append(dict(case=label,arm=arm,error=error,rollback=rollback,logs=list(logs),model_events=events,effects=effects,
                            alias_preserved=same_alias,stats=stats,sql=sql_events,unconsumed_pair=pending is not None,
                            user_records=[r for r in snapshot['tables']['analytics_userrecord'] if r['user_id']==1]))
                        # Aborted frames must not leak a pair into a later invocation.
                        pending=None
                        base.save('cases.json',results);transaction.set_rollback(True)
                    with connection.cursor() as c:
                        for seq,(value,called) in initial['sequences'].items():c.execute('SELECT setval(%s::regclass,%s,%s)',[seq,value,called])
                    check(label+'/'+arm+' full restoration',base.snapshot(connection)==initial)
        by={(r['case'],r['arm']):r for r in results};negative=[]
        fallback_cases=set(cases)-{'ordinary','missing','miss-mutates-lines','existing-overflow'}
        for label in cases:
            a=by[label,'original'];b=by[label,'guarded'];naive=by[label,'eager-negative-control']
            check(label+' values errors callbacks logs aliases equal',all(a[k]==b[k] for k in ['error','rollback','logs','model_events','effects','alias_preserved','user_records']))
            check(label+' complete 97 table 94 sequence state equal',json.loads((base.OUT/(label+'-original-full-state.json')).read_text())==json.loads((base.OUT/(label+'-guarded-full-state.json')).read_text()))
            check(label+' no unconsumed guarded handoff',not b['unconsumed_pair'])
            if label in fallback_cases:
                check(label+' effectful path retained with exact SQL',b['stats']['packets']==0 and bool(b['stats']['fallbacks']) and a['sql']==b['sql'])
            else:
                check(label+' one first-pair handoff consumed',b['stats']['packets']==b['stats']['consumed']==1)
                check(label+' exactly one physical call eliminated',len(a['sql'])==len(b['sql'])+1)
                if label in ['missing','miss-mutates-lines']:
                    check(label+' miss properties both reread',b['stats']['miss_lines']==b['stats']['miss_items']==1)
                    update=next(i for i,e in enumerate(b['sql']) if e['sql'].startswith('UPDATE "analytics_userrecord"'))
                    check(label+' original miss SQL follows UPDATE',[e['sql'] for e in b['sql'][update+1:update+3]]==[binding['count_sql'],binding['read_sql']])
            if any(a[k]!=naive[k] for k in ['error','model_events','effects','user_records']) or naive['unconsumed_pair']:
                negative.append(dict(case=label,original_error=a['error'],eager_error=naive['error'],
                    original_items=[r['num_order_items'] for r in a['user_records']],eager_items=[r['num_order_items'] for r in naive['user_records']],
                    premature_unconsumed_read=naive['unconsumed_pair']))
        check('all eight effect boundary negative controls detected',set(r['case'] for r in negative)==fallback_cases)
        check('miss trigger fresh value is twelve',by['miss-mutates-lines','guarded']['user_records'][0]['num_order_items']==12)
        check('normal packet result is six',by['ordinary','guarded']['stats']['values']==[[6,6]])
        check('properties and expression operations restored',Order.num_lines is old_lines and Order.num_items is old_items and receivers.F is F and all(getattr(c,n) is fn for c,n,fn in frozen))
        base.save('negative-controls.json',negative)
        base.save('result.json',dict(status='passed',checks=checks,cases=len(cases),arm_runs=len(results),admission=False,
            application_replacement=False,performance_claim=None,
            scope='Actual original receiver, frozen sources/managers/schema; single first-pair prototype, not whole-order integration',
            limits=['Server function permission diagnostics still differ; no error normalization or retry',
                    'Runtime checks cover tested effect sources, not arbitrary Python hooks, mutations or all exception ordering',
                    'Miss branch remains original; possible four-call saving is unexecuted at whole-order level',
                    'No performance measurement or change to original placement algorithm']))
    finally:
        base.ACTIVE=False;logger.removeHandler(handler);connection.close()


if __name__=='__main__':main()
