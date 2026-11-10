"""Bounded FK object-read differential and unchanged four-order replay."""
from collections import Counter
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
from types import MethodType
from unittest.mock import patch
import baseline_probe as base


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=base.TraceCursor,connection_factory=base.TraceConnection)
    django.setup()
    from django.db import connection,transaction
    from django.db.models import Model,QuerySet,signals
    from django.db.models.sql.query import Query
    from django.db.models.sql.compiler import SQLCompiler
    from django.db.models.fields.related_descriptors import ForwardManyToOneDescriptor
    from django.db.models.lookups import IntegerFieldExact
    from oscar.core.loading import get_model
    from foreign_key_read import installed,ORIGINAL
    records=json.loads(Path(os.environ['RDB_SELECT_RECORDS']).read_text())
    enabled=os.environ['RDB_FK_ARM']=='candidate';checks=[]
    def check(name,value):
        assert value,name
        checks.append(name)
    if sys.argv[1]=='replay':
        counts=Counter();old_profile=base.profile
        watched={fn.__code__:name for name,fn in [('get_select',SQLCompiler.get_select),('compile',SQLCompiler.compile),
                 ('build_filter',Query.build_filter),('get_converters',SQLCompiler.get_converters),
                 ('model_init',Model.__init__),('from_db',Model.from_db.__func__)]}
        def profile(frame,event,arg):
            old_profile(frame,event,arg)
            if event=='call' and frame.f_code in watched:counts[watched[frame.f_code]]+=1
        with (installed(records,lambda:base.ACTIVE) if enabled else nullcontext()) as state:
            with patch.object(base,'profile',profile):base.main()
        check('descriptor restored',ForwardManyToOneDescriptor.get_object is ORIGINAL)
        base.save('specialization.json',dict(counts=dict(counts),state=state.summary() if state else None,checks=checks))
        if enabled:check('35 real reads specialized with four cold plans',state.counts['specialized_reads']==35 and state.counts['cold_compilations']==4)
        base.save('specialization.json',dict(counts=dict(counts),state=state.summary() if state else None,checks=checks))
        return
    Partner=get_model('partner','Partner');Stock=get_model('partner','StockRecord')
    Product=get_model('catalogue','Product');OptionLink=get_model('basket','LineAttribute')
    initial=base.snapshot(connection);events=[];effects=[]
    def fields(obj):return dict(model=obj._meta.label,fields=base.encode({f.attname:getattr(obj,f.attname) for f in obj._meta.concrete_fields}),db=obj._state.db,adding=obj._state.adding)
    def observe(sender,instance,**kwargs):events.append(fields(instance))
    def read(value=1):return fields(Stock(partner_id=value).partner)
    def fresh():
        source=Stock(partner_id=1);a=source.partner;b=source.partner;c=Stock(partner_id=1).partner
        Partner.objects.filter(pk=1).update(name='new complete partner')
        d=Stock(partner_id=1).partner
        return dict(same_instance=a is b,distinct_instances=a is not c and c is not d,old=fields(a),new=fields(d))
    def other_models():
        return [fields(Stock(product_id=1).product),fields(Product(product_class_id=1).product_class),fields(OptionLink(option_id=1).option)]
    def mutates():
        def change(sender,instance,**kwargs):instance.name='changed by original initialization'
        signals.post_init.connect(change,sender=Partner,weak=False)
        try:return read()
        finally:signals.post_init.disconnect(change,sender=Partner)
    def fails():
        def fail(sender,instance,**kwargs):raise RuntimeError('original Partner initialization failure')
        signals.post_init.connect(fail,sender=Partner,weak=False)
        try:return read()
        finally:signals.post_init.disconnect(fail,sender=Partner)
    def extra_filter():
        field=Stock._meta.get_field('partner')
        def extra(this,instance):effects.append('extra-filter');return {'name':'no matching partner'}
        with patch.object(field,'get_extra_descriptor_filter',MethodType(extra,field)):return read()
    def custom_lookup():
        class Effectful(IntegerFieldExact):
            def as_sql(self,compiler,connection):effects.append('custom-lookup');return super().as_sql(compiler,connection)
        pk=Partner._meta.pk;pk.register_lookup(Effectful)
        try:return read()
        finally:pk._unregister_lookup(Effectful)
    def custom_queryset():
        class Custom(QuerySet):
            def get(self,*a,**kw):effects.append('custom-queryset');raise RuntimeError('custom queryset get')
        with patch.object(Partner._base_manager,'_queryset_class',Custom):return read()
    def deferred_source():
        source=Stock.objects.only('id').get(pk=1);before=sorted(source.get_deferred_fields())
        value=fields(source.partner)
        return dict(before=before,after=sorted(source.get_deferred_fields()),value=value)
    def converter(fail=False):
        field=Product._meta.get_field('upc')
        def convert(this,value,expression,connection):
            effects.append(dict(converter='upc',value=value,column=expression.target.column))
            if fail:raise RuntimeError('original UPC conversion failure')
            return 'converted:'+str(value)
        with patch.object(field,'from_db_value',MethodType(convert,field)):
            return fields(Stock(product_id=1).product)
    cases=[('fresh-cache-and-read-after-write',fresh),('other-full-models',other_models),
           ('missing',lambda:read(99999999)),('null',lambda:read(None)),('negative',lambda:read(-1)),
           ('overflow',lambda:read(2**63)),('invalid',lambda:read('not-an-integer')),
           ('init-mutates',mutates),('init-fails',fails),('extra-filter',extra_filter),
           ('custom-lookup',custom_lookup),('custom-queryset',custom_queryset),('deferred-source',deferred_source),
           ('converter-mutates',converter),('converter-fails',lambda:converter(True))]
    results=[]
    with (installed(records,lambda:base.ACTIVE) if enabled else nullcontext()) as state:
        for label,action in cases:
            events.clear();effects.clear();start=len(base.EVENTS)
            with transaction.atomic():
                signals.post_init.connect(observe,sender=Partner,weak=False)
                base.ACTIVE=True
                try:value=action();error=None
                except Exception as exc:value=None;error=dict(type=type(exc).__qualname__,message=str(exc))
                finally:
                    base.ACTIVE=False
                    signals.post_init.disconnect(observe,sender=Partner)
                results.append(dict(case=label,value=base.encode(value),error=error,events=list(events),effects=list(effects),
                    sql=[dict(sql=e['sql'],params=e['params']) for e in base.EVENTS[start:] if e['kind']=='execute']))
                base.save('cases.json',results);base.save(label+'-full-state.json',base.snapshot(connection))
                transaction.set_rollback(True)
            check(label+' complete state restored',base.snapshot(connection)==initial)
    check('descriptor restored after local cases',ForwardManyToOneDescriptor.get_object is ORIGINAL)
    base.save('local-observations.json',dict(cases=results,state=state.summary() if state else None))
    if enabled:
        check('specialized reads actually executed',state.counts['specialized_reads']>=8)
        check('custom descriptor/lookup/manager and integer boundaries retained',all(state.fallbacks[k]>0 for k in ['relation-effects','input-or-lookup','manager-router-or-inheritance','integer-domain']))
    base.save('local-result.json',dict(status='passed',checks=checks,cases=results,state=state.summary() if state else None))
    connection.close()


if __name__=='__main__':main()
