"""Fixed paired order-placement timing, with untraced physical call counters."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import time

import psycopg2.extensions
import baseline_probe as base
from allocation_handoff import installed

OUT=Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
PROTOCOL=json.loads(Path(os.environ['RDB_PERFORMANCE_PROTOCOL']).read_text())
BINDING=json.loads(Path(os.environ['RDB_HANDOFF_BINDING']).read_text())
COUNTS=dict(active=False,execute=0,executemany=0,commit=0,rollback=0)


class Cursor(psycopg2.extensions.cursor):
    def execute(self,query,vars=None):
        if COUNTS['active']:COUNTS['execute']+=1
        return super().execute(query,vars)
    def executemany(self,query,vars_list):
        if COUNTS['active']:COUNTS['executemany']+=1
        return super().executemany(query,vars_list)


class Connection(psycopg2.extensions.connection):
    def commit(self):
        if COUNTS['active']:COUNTS['commit']+=1
        return super().commit()
    def rollback(self):
        if COUNTS['active']:COUNTS['rollback']+=1
        return super().rollback()


# A default argument preserves the real clock when the business clock is frozen.
def now(clock=time.perf_counter_ns):return clock()


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=Cursor,connection_factory=Connection)
    django.setup()
    from django.db import connection,transaction
    from django.contrib.auth import get_user_model
    from django.contrib.sites.models import Site
    from freezegun import freeze_time
    from oscar.core.loading import get_model,get_class
    from oscar.apps.order.utils import OrderCreator
    from oscar.apps.checkout.calculators import OrderTotalCalculator
    from oscar.apps.shipping.methods import Free
    Stock=get_model('partner','StockRecord');Basket=get_model('basket','Basket')
    Strategy=get_class('partner.strategy','Selector')
    SurchargeApplicator=get_class('checkout.applicator','SurchargeApplicator')
    assert base.code_hash(Stock.allocate.__code__)==BINDING['allocate_code_hash']
    assert sys.getprofile() is None and sys.gettrace() is None and not base.ACTIVE
    inputs=json.loads(Path(os.environ['RDB_REQUESTS']).read_text())
    fixture=json.loads(Path(os.environ['RDB_FIXTURE_STATE']).read_text())
    expected=json.loads(Path(os.environ['RDB_EXPECTED_STATE']).read_text())
    checks=[];runs=[]
    def check(label,ok):
        assert ok,label
        checks.append(label)
    # Guard against accidentally measuring freezegun's constant clock.
    with freeze_time('2026-11-06 01:00:00+00:00'):
        start=now();time.sleep(.003);elapsed=now()-start
        check('real monotonic timer advances while business clock is frozen',elapsed>=1_000_000)
    base.save('measurement-runtime.json',dict(python=sys.version,django=django.get_version(),psycopg2=psycopg2.__version__,
        debug=settings.DEBUG,atomic_requests=settings.DATABASES['default']['ATOMIC_REQUESTS'],
        instrumentation='physical execute/commit integer counters only; no SQL stack tracing or profiler',
        clock='real perf_counter_ns saved as function default; business timestamps fixed to prior baseline',
        scope='OrderCreator.place_order plus request-equivalent outer atomic and real commit; pricing/setup excluded from primary, included in secondary preparation-plus-placement timing',
        trial_conditions='one persistent Python process; each arm uses an independent fixture-cloned database and fresh connection/backend; Site cache cleared per trial; first-use query/function planning included'))
    for item in PROTOCOL['schedule']:
        trial=OUT/item['id'];trial.mkdir()
        connection.close();connection.settings_dict['NAME']=item['database']
        Site.objects.clear_cache()
        before=base.snapshot(connection)
        check(item['id']+' exact starting tables and sequences',before==fixture)
        (trial/'before-full-state.json').write_text(json.dumps(before,sort_keys=True,ensure_ascii=False)+'\n')
        with connection.cursor() as c:
            c.execute('SHOW transaction_isolation');check(item['id']+' original isolation',c.fetchone()[0]=='read committed')
            c.execute("SELECT has_function_privilege(current_user,'public.reversedb_allocate_packet(integer,bigint)','EXECUTE')")
            check(item['id']+' verified function execution privilege',c.fetchone()[0])
        measured=[];objects=[]
        context=installed(BINDING) if item['arm']=='candidate' else nullcontext({})
        with context as stats,freeze_time('2026-11-06 01:00:00+00:00'):
            for i,number in enumerate(inputs['order_numbers']):
                prepare_start=now()
                user=get_user_model().objects.get(pk=inputs['users'][i%2])
                basket=Basket.objects.get(pk=inputs['baskets'][i]);basket.strategy=Strategy().strategy(user=user)
                shipping=Free();charge=shipping.calculate(basket)
                surcharges=SurchargeApplicator().get_applicable_surcharges(basket)
                total=OrderTotalCalculator().calculate(basket=basket,shipping_charge=charge,surcharges=surcharges)
                lines=list(basket.all_lines());stocks=[line.stockrecord for line in lines]
                args=dict(basket=basket,total=total,shipping_method=shipping,shipping_charge=charge,user=user,order_number=number,surcharges=surcharges)
                for k in ['execute','executemany','commit','rollback']:COUNTS[k]=0
                COUNTS['active']=True
                start=now()
                try:
                    with transaction.atomic():order=OrderCreator().place_order(**args)
                finally:
                    stop=now();COUNTS['active']=False
                measured.append(dict(number=number,placement_ns=stop-start,prepare_and_place_ns=stop-prepare_start,
                                     calls={k:v for k,v in COUNTS.items() if k!='active'}))
                # Correctness observations and DB reads are outside timed intervals.
                check(item['id']+'/'+number+' original basket line order',list(order.lines.values_list('product_id',flat=True))==[l.product_id for l in lines])
                check(item['id']+'/'+number+' original stock instances',all(l.stockrecord is s for l,s in zip(lines,stocks)))
                check(item['id']+'/'+number+' complete refresh values',all(s.num_allocated==Stock.objects.get(pk=s.pk).num_allocated for s in stocks))
                objects.append(base.encode({f.attname:getattr(order,f.attname) for f in order._meta.concrete_fields}))
        counts=[x['calls']['execute'] for x in measured]
        check(item['id']+' exact physical execute counts',counts==([29,53,88,17] if item['arm']=='original' else [28,50,82,17]))
        check(item['id']+' actual commits retained',all(m['calls']['commit']==1 and m['calls']['rollback']==0 and m['calls']['executemany']==0 for m in measured))
        if item['arm']=='candidate':check(item['id']+' actual handoff without fallback',stats==dict(fused_updates=10,reused_reads=10,fallbacks=[],leftover_results=0))
        after=base.snapshot(connection)
        check(item['id']+' all final tables and sequences match original baseline',after==expected)
        (trial/'after-full-state.json').write_text(json.dumps(after,sort_keys=True,ensure_ascii=False)+'\n')
        result=dict(**item,orders=measured,objects=objects,handoff_stats=stats,
            placement_ns=sum(x['placement_ns'] for x in measured),prepare_and_place_ns=sum(x['prepare_and_place_ns'] for x in measured))
        (trial/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');runs.append(result)
        print(item['id']+' completed',flush=True)
    connection.close()
    base.save('result.json',dict(status='passed',checks=checks,runs=runs,
        limits=['fixed small fixture; sequential placement latency, not concurrent throughput or full HTTP checkout',
                'extra function error CONTEXT is a retained mismatch, not resolved by successful timing trials',
                'fresh database backend per arm includes first-use planning; not a long-running warm-connection benchmark']))


if __name__=='__main__':main()
