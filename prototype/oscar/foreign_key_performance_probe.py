"""Fixed FK query-construction specialization timing; real wall/thread CPU, no profiling or tracing."""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys
import time

import psycopg2.extensions
import baseline_probe as base

OUT=Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
PROTOCOL=json.loads(Path(os.environ['RDB_PERFORMANCE_PROTOCOL']).read_text())
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
def cpu_now(clock=time.thread_time_ns):return clock()


@contextmanager
def timed_context(context,overhead):
    start=now();value=context.__enter__();overhead['setup_ns']=now()-start
    try:
        yield value
    finally:
        start=now();context.__exit__(*sys.exc_info());overhead['teardown_ns']=now()-start


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=Cursor,connection_factory=Connection)
    django.setup()
    from foreign_key_read import installed,ORIGINAL
    from django.db.models.fields.related_descriptors import ForwardManyToOneDescriptor
    from django.db import connection,transaction
    from django.contrib.auth import get_user_model
    from django.contrib.sites.models import Site
    from freezegun import freeze_time
    from oscar.core.loading import get_model,get_class
    from oscar.apps.order.utils import OrderCreator
    from oscar.apps.checkout.calculators import OrderTotalCalculator
    from oscar.apps.shipping.methods import Free
    Stock=get_model('partner','StockRecord');Basket=get_model('basket','Basket')
    records=json.loads(Path(os.environ['RDB_SELECT_RECORDS']).read_text())
    Strategy=get_class('partner.strategy','Selector')
    SurchargeApplicator=get_class('checkout.applicator','SurchargeApplicator')
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
        start=now();cpu_start=cpu_now()
        for _ in range(30000):pass
        time.sleep(.003);elapsed=now()-start
        check('real wall and thread CPU clocks advance under frozen business time',elapsed>=1_000_000 and cpu_now()>cpu_start)
    base.save('measurement-runtime.json',dict(python=sys.version,django=django.get_version(),psycopg2=psycopg2.__version__,
        debug=settings.DEBUG,atomic_requests=settings.DATABASES['default']['ATOMIC_REQUESTS'],
        instrumentation='physical execute/commit integer counters only; no SQL stack tracing or profiler',
        clock='real perf_counter_ns and thread_time_ns saved as defaults; fixed business timestamps',
        scope='Primary: four OrderCreator placements with atomic/commit plus adapter setup/teardown; pricing preparation excluded; secondary raw placement/preparation/thread CPU exclude adapter setup',
        trial_conditions='one persistent Python process; per-arm fixture-cloned DB/fresh connection; Site cache reset per trial; exact validated adapter; setup/teardown measured separately and included in primary total; correctness checks outside timers'))
    for item in PROTOCOL['schedule']:
        trial=OUT/item['id'];trial.mkdir()
        connection.close();connection.settings_dict['NAME']=item['database']
        Site.objects.clear_cache()
        before=base.snapshot(connection)
        check(item['id']+' exact starting tables and sequences',before==fixture)
        (trial/'before-full-state.json').write_text(json.dumps(before,sort_keys=True,ensure_ascii=False)+'\n')
        with connection.cursor() as c:
            c.execute('SHOW transaction_isolation');check(item['id']+' original isolation',c.fetchone()[0]=='read committed')
        measured=[];objects=[]
        overhead={}
        context=installed(records,lambda:COUNTS['active']) if item['arm']=='candidate' else nullcontext(None)
        with timed_context(context,overhead) as stats,freeze_time('2026-11-06 01:00:00+00:00'):
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
                cpu_start=cpu_now();start=now()
                try:
                    with transaction.atomic():order=OrderCreator().place_order(**args)
                finally:
                    stop=now();cpu_stop=cpu_now();COUNTS['active']=False
                measured.append(dict(number=number,placement_ns=stop-start,thread_cpu_ns=cpu_stop-cpu_start,prepare_and_place_ns=stop-prepare_start,
                                     calls={k:v for k,v in COUNTS.items() if k!='active'}))
                # Correctness observations and DB reads are outside timed intervals.
                check(item['id']+'/'+number+' original basket line order',list(order.lines.values_list('product_id',flat=True))==[l.product_id for l in lines])
                check(item['id']+'/'+number+' original stock instances',all(l.stockrecord is s for l,s in zip(lines,stocks)))
                check(item['id']+'/'+number+' complete refresh values',all(s.num_allocated==Stock.objects.get(pk=s.pk).num_allocated for s in stocks))
                objects.append(base.encode({f.attname:getattr(order,f.attname) for f in order._meta.concrete_fields}))
        counts=[x['calls']['execute'] for x in measured]
        check(item['id']+' exact physical execute counts',counts==[29,53,88,17])
        check(item['id']+' actual commits retained',all(m['calls']['commit']==1 and m['calls']['rollback']==0 and m['calls']['executemany']==0 for m in measured))
        if stats is not None:
            check(item['id']+' 35 FK reads and four original cold compilations',stats.counts['specialized_reads']==35 and stats.counts['cold_compilations']==4)
            check(item['id']+' all intended fixed workload reads admitted',not stats.fallbacks)
        check(item['id']+' original descriptor restored and profiling absent',ForwardManyToOneDescriptor.get_object is ORIGINAL and sys.getprofile() is None and sys.gettrace() is None)
        after=base.snapshot(connection)
        check(item['id']+' all final tables and sequences match original baseline',after==expected)
        (trial/'after-full-state.json').write_text(json.dumps(after,sort_keys=True,ensure_ascii=False)+'\n')
        result=dict(**item,orders=measured,objects=objects,specialization=stats.summary() if stats is not None else None,adapter_overhead=overhead,
            placement_ns=sum(x['placement_ns'] for x in measured)+overhead['setup_ns']+overhead['teardown_ns'],
            placement_only_ns=sum(x['placement_ns'] for x in measured),thread_cpu_ns=sum(x['thread_cpu_ns'] for x in measured),prepare_and_place_ns=sum(x['prepare_and_place_ns'] for x in measured))
        (trial/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');runs.append(result)
        print(item['id']+' completed',flush=True)
    connection.close()
    base.save('result.json',dict(status='passed',checks=checks,runs=runs,
        limits=['fixed small fixture; sequential placement latency, not concurrent throughput or full HTTP checkout',
                'source-bound FK adapter only; guards, four cold compilations, converter/model reconstruction and setup/teardown included in primary; no production admission',
                'thread CPU is the application thread only, not PostgreSQL/server CPU',
                'fresh database backend per arm includes first-use planning; not a long-running warm-connection benchmark']))


if __name__=='__main__':main()
