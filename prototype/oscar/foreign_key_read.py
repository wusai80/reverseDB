"""Source-bound experiment: specialize a whole plain FK-read construction path.

Original descriptor cache/null semantics and original full model construction
remain. Recipes are compiled by Django, contain no row values, and execute every
read. This single-process scoped prototype is not a production admission gate.
"""
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.db.models import AutoField,BigAutoField,ForeignKey,Manager,Model,QuerySet
from django.db.models.fields import IntegerField
from django.db.models.fields.related import ForeignObject
from django.db.models.fields.related_descriptors import ForwardManyToOneDescriptor,ForeignKeyDeferredAttribute
from django.db.models.lookups import IntegerFieldExact
from django.db.models.expressions import Col
from django.db.models.sql.compiler import SQLCompiler

ORIGINAL=ForwardManyToOneDescriptor.get_object
GET_QUERYSET=ForwardManyToOneDescriptor.get_queryset
EXTRA=ForeignObject.get_extra_descriptor_filter
REVERSE=ForeignObject.get_reverse_related_filter
MANAGER_QUERYSET=Manager.get_queryset
PREP=IntegerField.get_prep_value
DB_PREP=AutoField.get_db_prep_value


class Reads:
    def __init__(self,records,active):
        self.active=active;self.recipes={};self.plans={};self.counts=Counter();self.fallbacks=Counter()
        self.observations=[]
        for r in records:
            if r['kind']=='select_compile' and r['facts']['integer_pk_point_candidate']:
                self.recipes.setdefault(r['facts']['model'],[]).append(r)

    def fallback(self,reason,descriptor,instance):
        self.fallbacks[reason]+=1
        return ORIGINAL(descriptor,instance)

    def get(self,descriptor,instance):
        if not self.active():return ORIGINAL(descriptor,instance)
        field=descriptor.field;model=field.remote_field.model;pk=model._meta.pk
        if (type(descriptor) is not ForwardManyToOneDescriptor or type(field) is not ForeignKey
            or getattr(descriptor.get_queryset,'__func__',None) is not GET_QUERYSET
            or getattr(field.get_extra_descriptor_filter,'__func__',None) is not EXTRA
            or getattr(field.get_reverse_related_filter,'__func__',None) is not REVERSE
            or type(instance).__getattribute__ is not object.__getattribute__
            or type(getattr(type(instance),field.attname,None)) is not ForeignKeyDeferredAttribute
            or field.target_field is not pk or tuple(field.related_fields)!=((field,pk),)):
            return self.fallback('relation-effects',descriptor,instance)
        if (settings.DATABASE_ROUTERS or instance._state.db not in (None,'default') or connection.vendor!='postgresql'
            or type(model._base_manager) is not Manager or model._base_manager._db is not None
            or model._base_manager._queryset_class is not QuerySet
            or getattr(model._base_manager.get_queryset,'__func__',None) is not MANAGER_QUERYSET
            or model._meta.parents or model._meta.proxy):
            return self.fallback('manager-router-or-inheritance',descriptor,instance)
        value=instance.__dict__.get(field.attname)
        if (type(pk) not in (AutoField,BigAutoField) or type(value) is not int
            or getattr(pk.get_prep_value,'__func__',None) is not PREP
            or getattr(pk.get_db_prep_value,'__func__',None) is not DB_PREP
            or pk.get_lookup('exact') is not IntegerFieldExact):
            return self.fallback('input-or-lookup',descriptor,instance)
        lo,hi=connection.ops.integer_field_range(pk.get_internal_type())
        if not lo<=value<=hi:return self.fallback('integer-domain',descriptor,instance)
        if model._meta.label not in self.recipes:return self.fallback('no-source-recipe',descriptor,instance)
        plan=self.plans.get(model)
        if plan is None:
            # One cold compile per target model; preserve complete source SQL,
            # not a handwritten query or observed-ID table. No database read.
            compiler=model._base_manager.filter(pk=0).order_by()[:21].query.get_compiler(using='default')
            sql,params=compiler.as_sql()
            fields=model._meta.concrete_fields
            if (params!=(0,)
                or compiler.klass_info!={'model':model,'select_fields':list(range(len(fields)))}
                or compiler.annotation_col_map or [s[0].target for s in compiler.select]!=list(fields)
                or not all(type(s[0]) is Col for s in compiler.select)
                or not any(r['sql']==sql and len(r['facts']['result_abi']['select'])==len(fields) for r in self.recipes[model._meta.label])):
                return self.fallback('projection-or-conversion',descriptor,instance)
            plan=dict(sql=sql,fields=tuple(f.attname for f in fields),model_from_db=model.from_db.__func__,
                      columns=tuple((s[0].alias,s[0].target,s[0].output_field) for s in compiler.select))
            self.plans[model]=plan;self.counts['cold_compilations']+=1
        if model.from_db.__func__ is not plan['model_from_db']:
            return self.fallback('changed-materializer',descriptor,instance)
        # Original integer preparation and DB adaptation remain. Non-native
        # integers, overflow and custom lookups were sent to the original path.
        prepared=pk.get_prep_value(value)
        parameter=pk.get_db_prep_value(prepared,connection,prepared=True)
        with connection.cursor() as cursor:
            cursor.execute(plan['sql'],[parameter]);rows=cursor.fetchall()
        # Keep original converter discovery AND invocation after the actual read.
        # Fresh Cols avoid leaking mutable expression identity across reads.
        decoder=SimpleNamespace(connection=connection)
        expressions=[Col(*column) for column in plan['columns']]
        converters=SQLCompiler.get_converters(decoder,expressions)
        if converters:rows=SQLCompiler.apply_converters(decoder,rows,converters)
        objects=[model.from_db('default',list(plan['fields']),row[:len(plan['fields'])]) for row in rows]
        self.counts['specialized_reads']+=1
        self.observations.append(dict(model=model._meta.label,field=field.model._meta.label+'.'+field.name,rows=len(objects)))
        if len(objects)==1:return objects[0]
        if not objects:raise model.DoesNotExist('%s matching query does not exist.' % model._meta.object_name)
        raise model.MultipleObjectsReturned('get() returned more than one %s -- it returned %s!' %
            (model._meta.object_name,len(objects) if len(objects)<21 else 'more than 20'))

    def summary(self):
        return dict(counts=dict(self.counts),fallbacks=dict(self.fallbacks),reads=self.observations,
                    plans={m._meta.label:dict(sql=p['sql'],fields=p['fields']) for m,p in self.plans.items()})


@contextmanager
def installed(records,active=lambda:True):
    assert ForwardManyToOneDescriptor.get_object is ORIGINAL,'Overlapping experiment installation'
    state=Reads(records,active)
    def get(descriptor,instance):return state.get(descriptor,instance)
    with patch.object(ForwardManyToOneDescriptor,'get_object',get):yield state
    assert ForwardManyToOneDescriptor.get_object is ORIGINAL
