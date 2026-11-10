"""Run original/candidate locally and through the already fixed order replay."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys

import baseline_probe as base
from allocation_handoff import CALL, PACKET_SQL, installed

OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
BINDING = json.loads(Path(os.environ['RDB_HANDOFF_BINDING']).read_text())
ENABLED = os.environ['RDB_HANDOFF_ARM'] == 'candidate'


def local_cases():
    from django.db import connection, transaction
    from django.db.models.signals import pre_save, post_save, post_init
    from oscar.core.loading import get_model
    from freezegun import freeze_time
    Stock = get_model('partner', 'StockRecord')
    checks=[]; results=[]
    def check(label, ok):
        assert ok,label
        checks.append(label)
    before=base.snapshot(connection);base.save('before-full-state.json',before)
    cases=[dict(name='ordinary', initial=4, qty=3),dict(name='null',initial=None,qty=3),
           dict(name='missing',initial=4,qty=3,missing=True),dict(name='overflow',initial=2147483647,qty=1),
           dict(name='after-trigger',initial=4,qty=3,trigger='after'),dict(name='skip-trigger',initial=4,qty=3,trigger='skip'),
           dict(name='post-init-mutation',initial=4,qty=3,init='mutate'),
           dict(name='post-init-failure',initial=4,qty=3,init='raise'),
           dict(name='pre-save-failure',initial=4,qty=3,pre='raise'),
           dict(name='wide-int-fallback',initial=-2147483648,qty=2147483648),
           dict(name='custom-execute-wrapper',initial=4,qty=3,wrapper=True)]
    with freeze_time('2026-11-07 01:00:00+00:00'):
        for case in cases:
            events=[]; originals=[]; queries=[]; error=None; state_after=None
            def init_observer(sender,instance,**kwargs):
                originals.append(instance)
                partial='price' not in instance.__dict__
                events.append(dict(kind='post_init',partial=partial,same=instance is obj,allocated=instance.__dict__.get('num_allocated')))
                if partial and case.get('init')=='mutate':instance.num_allocated=42
                if partial and case.get('init')=='raise':raise RuntimeError('synthetic post-init failure')
            def pre_observer(sender,instance,**kwargs):
                events.append(dict(kind='pre_save',same=instance is obj,allocated=instance.num_allocated))
                if case.get('pre')=='raise':raise RuntimeError('synthetic pre-save failure')
            def post_observer(sender,instance,**kwargs):
                events.append(dict(kind='post_save',same=instance is obj,allocated=instance.num_allocated))
            def execute_observer(execute,statement,params,many,context):
                queries.append(dict(sql=statement,params=params))
                return execute(statement,params,many,context)
            with transaction.atomic():
                Stock.objects.filter(pk=2).update(num_allocated=case['initial'],low_stock_threshold=None)
                obj=Stock.objects.select_related('product__product_class','partner').get(pk=2)
                product_alias,partner_alias=obj.product,obj.partner
                obj.can_track_allocations
                if case.get('missing'):obj.pk=9000000000
                if case.get('trigger'):
                    body='RETURN NULL;' if case['trigger']=='skip' else 'IF pg_trigger_depth()=1 THEN UPDATE public.partner_stockrecord SET num_allocated=num_allocated+100 WHERE id=NEW.id; END IF; RETURN NEW;'
                    with connection.cursor() as c:
                        c.execute('CREATE FUNCTION public.reversedb_handoff_trigger() RETURNS trigger LANGUAGE plpgsql AS $b$ BEGIN '+body+' END $b$')
                        c.execute('CREATE TRIGGER reversedb_handoff_trigger '+('BEFORE' if case['trigger']=='skip' else 'AFTER')+' UPDATE ON partner_stockrecord FOR EACH ROW EXECUTE FUNCTION public.reversedb_handoff_trigger()')
                pre_save.connect(pre_observer,sender=Stock,weak=False)
                post_save.connect(post_observer,sender=Stock,weak=False)
                post_init.connect(init_observer,sender=Stock,weak=False)
                start=len(base.EVENTS)
                stats={}
                try:
                    with (installed(BINDING) if ENABLED else nullcontext({})) as stats:
                        with connection.execute_wrapper(execute_observer) if case.get('wrapper') else nullcontext():
                            base.ACTIVE=True
                            # The inner savepoint lets us inspect complete state after SQL errors.
                            with transaction.atomic():obj.allocate(case['qty'])
                except Exception as exc:
                    cause=exc.__cause__
                    primary=getattr(getattr(cause,'diag',None),'message_primary',None)
                    error=dict(type=type(exc).__qualname__,message=primary or str(exc),sqlstate=getattr(cause,'pgcode',None),full_text=str(exc))
                finally:
                    base.ACTIVE=False
                    pre_save.disconnect(pre_observer,sender=Stock)
                    post_save.disconnect(post_observer,sender=Stock)
                    post_init.disconnect(init_observer,sender=Stock)
                trace=base.EVENTS[start:];base.save(case['name']+'-trace.json',trace)
                base.save(case['name']+'-observation.json',dict(error=error,events=events,stats=stats))
                state_after=base.snapshot(connection)
                check(case['name']+' original identity and relation aliases',obj.product is product_alias and obj.partner is partner_alias)
                if ENABLED:
                    check(case['name']+' no leftover row packet',stats['leftover_results']==0)
                    if case['name'] in ['wide-int-fallback','custom-execute-wrapper']:
                        check(case['name']+' original path retained',stats['fused_updates']==stats['reused_reads']==0 and len(stats['fallbacks'])==1)
                    elif case['name'] not in ['pre-save-failure','overflow']:
                        check(case['name']+' actual packet consumed by original refresh',stats['fused_updates']==stats['reused_reads']==1)
                base.save(case['name']+'-full-state.json',state_after)
                results.append(dict(case=case['name'],events=events,error=error,fields=base.encode({f.attname:obj.__dict__[f.attname] for f in obj._meta.concrete_fields if f.attname in obj.__dict__}),
                    cached_relations=sorted(obj._state.fields_cache),database=obj._state.db,
                    physical_execute_count=sum(e['kind']=='execute' for e in trace),stats=stats,wrapper_queries=queries))
                transaction.set_rollback(True)
    after=base.snapshot(connection);base.save('after-full-state.json',after)
    check('full original fixture preserved after local cases',before==after)
    base.save('local-result.json',dict(status='passed',checks=checks,cases=results))


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=base.TraceCursor,connection_factory=base.TraceConnection)
    django.setup()
    from django.db import connection
    from oscar.core.loading import get_model
    actual_hash=base.code_hash(get_model('partner','StockRecord').allocate.__code__)
    assert actual_hash==BINDING['allocate_code_hash'], 'Executing allocate differs from bound baseline bytecode'
    base.save('binding-verification.json',dict(actual_allocate_code_hash=actual_hash,expected_allocate_code_hash=BINDING['allocate_code_hash'],matched=True))
    with connection.cursor() as c:
        c.execute(PACKET_SQL)
        c.execute('REVOKE ALL ON FUNCTION public.reversedb_allocate_packet(integer,bigint) FROM PUBLIC')
    if sys.argv[1]=='local':
        local_cases()
    else:
        with (installed(BINDING) if ENABLED else nullcontext({})) as stats:
            base.main(allocation_statement_prefix=CALL if ENABLED else 'UPDATE "partner_stockrecord"')
        base.save('handoff-stats.json',stats)
        result=json.loads((OUT/'result.json').read_text())
        result['execution_arm']='experimental-handoff' if ENABLED else 'original'
        base.save('result.json',result)
        if ENABLED:assert stats['fused_updates']==stats['reused_reads']==10 and stats['leftover_results']==0 and not stats['fallbacks']
    connection.close()


if __name__=='__main__':main()
