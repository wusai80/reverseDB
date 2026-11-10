"""CPU module/call-edge profiles, wall DB boundaries, and server statement time.

No module removal. Deterministic cProfile overhead is measured with controls;
server/client intervals overlap and are deliberately reported separately.
"""
from contextlib import nullcontext
from collections import defaultdict
import cProfile
import importlib.util
import json
import marshal
import os
from pathlib import Path
import sys
import time
import types

import psycopg2.extensions
import baseline_probe as base
from allocation_handoff import installed

OUT=Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
PROTOCOL=json.loads(Path(os.environ['RDB_PROFILE_PROTOCOL']).read_text())
BINDING=json.loads(Path(os.environ['RDB_HANDOFF_BINDING']).read_text())
STATE=dict(active=False,profiled=False,events=[],codes={})


def wall(clock=time.perf_counter_ns):return clock()
def cpu(clock=time.thread_time_ns):return clock()


def ref(code):
    key=str(id(code))
    STATE['codes'][key]=code
    return key


def callers():
    result=[];frame=sys._getframe(2)
    while frame:
        module=frame.f_globals.get('__name__','')
        if module.startswith(('oscar.','django.','allocation_handoff')):
            result.append(dict(code=ref(frame.f_code),module=module,offset=frame.f_lasti))
        frame=frame.f_back
    return result


class Cursor(psycopg2.extensions.cursor):
    def execute(self,query,vars=None):
        if not STATE['active']:return super().execute(query,vars)
        stack=callers() if STATE['profiled'] else []
        w,c=wall(),cpu()
        try:return super().execute(query,vars)
        finally:
            elapsed_cpu,elapsed_wall=cpu()-c,wall()-w
            STATE['events'].append(dict(kind='execute',sql=str(query),params=base.encode(vars),
                                       wall_ns=elapsed_wall,cpu_ns=elapsed_cpu,stack=stack))


class Connection(psycopg2.extensions.connection):
    def commit(self):
        if not STATE['active']:return super().commit()
        w,c=wall(),cpu()
        try:return super().commit()
        finally:STATE['events'].append(dict(kind='commit',wall_ns=wall()-w,cpu_ns=cpu()-c,stack=[]))


def code_metadata(code):
    if isinstance(code,str):return dict(native=True,name=code,module='native')
    path=code.co_filename
    if '/site-packages/' in path:module=path.split('/site-packages/',1)[1]
    elif '/src/oscar/' in path:module='oscar/'+path.split('/src/oscar/',1)[1]
    elif '/prototype/oscar/' in path:module='experiment/'+Path(path).name
    else:module=path
    module=module.removesuffix('.py').replace('/','.')
    return dict(native=False,name=code.co_qualname,module=module,file=path,line=code.co_firstlineno,
                code_sha256=base.code_hash(code),bytecode_hex=code.co_code.hex(),exception_table_hex=code.co_exceptiontable.hex())


def export_profile(profiler):
    records=[];edges=[]
    for item in profiler.getstats():
        key=ref(item.code) if not isinstance(item.code,str) else 'native:'+item.code
        records.append(dict(id=key,**code_metadata(item.code),calls=item.callcount,recursive_calls=item.reccallcount,
                            self_cpu_ms=item.inlinetime*1000,inclusive_cpu_ms=item.totaltime*1000))
        for sub in item.calls or []:
            callee=ref(sub.code) if not isinstance(sub.code,str) else 'native:'+sub.code
            edges.append(dict(caller=key,callee=callee,calls=sub.callcount,recursive_calls=sub.reccallcount,
                              inclusive_cpu_ms=sub.totaltime*1000))
    return dict(functions=records,edges=edges,
        limits='self CPU can be summed; inclusive CPU/edge times overlap and must not be summed. Aggregated call graph can contain cycles; it is not an execution-order DAG.')


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
    Strategy=get_class('partner.strategy','Selector');SurchargeApplicator=get_class('checkout.applicator','SurchargeApplicator')
    assert base.code_hash(Stock.allocate.__code__)==BINDING['allocate_code_hash']
    requests=json.loads(Path(os.environ['RDB_REQUESTS']).read_text())
    fixture=json.loads(Path(os.environ['RDB_FIXTURE_STATE']).read_text())
    expected=json.loads(Path(os.environ['RDB_EXPECTED_STATE']).read_text())
    checks=[];runs=[]
    def check(label,ok):
        assert ok,label
        checks.append(label)
    admin=psycopg2.connect(host=os.environ['DATABASE_HOST'],port=os.environ['DATABASE_PORT'],dbname='oscar_fixture',
                          user='postgres',password=os.environ['RDB_PROFILE_ADMIN_PASSWORD'])
    admin.autocommit=True
    with freeze_time('2026-11-06 01:00:00+00:00'):
        w,c=wall(),cpu()
        for _ in range(20000):pass
        check('real clocks advance under business time freeze',wall()>w and cpu()>c)
    for trial in PROTOCOL['schedule']:
        folder=OUT/trial['id'];folder.mkdir()
        connection.close();connection.settings_dict['NAME']=trial['database'];Site.objects.clear_cache()
        before=base.snapshot(connection);check(trial['id']+' complete initial fixture',before==fixture)
        (folder/'before-full-state.json').write_text(json.dumps(before,sort_keys=True)+'\n')
        STATE['events']=[];STATE['profiled']=trial['profiled']
        prof=cProfile.Profile(timer=time.thread_time_ns,timeunit=1e-9,subcalls=True,builtins=True)
        objects=[];orders=[];statements=[]
        context=installed(BINDING) if trial['arm']=='candidate' else nullcontext({})
        with context as stats,freeze_time('2026-11-06 01:00:00+00:00'):
            for i,number in enumerate(requests['order_numbers']):
                user=get_user_model().objects.get(pk=requests['users'][i%2])
                basket=Basket.objects.get(pk=requests['baskets'][i]);basket.strategy=Strategy().strategy(user=user)
                shipping=Free();charge=shipping.calculate(basket);surcharges=SurchargeApplicator().get_applicable_surcharges(basket)
                total=OrderTotalCalculator().calculate(basket=basket,shipping_charge=charge,surcharges=surcharges)
                lines=list(basket.all_lines());stocks=[l.stockrecord for l in lines]
                args=dict(basket=basket,total=total,shipping_method=shipping,shipping_charge=charge,user=user,order_number=number,surcharges=surcharges)
                with admin.cursor() as c:c.execute('SELECT pg_stat_statements_reset()')
                event_start=len(STATE['events']);STATE['active']=True
                if trial['profiled']:prof.enable()
                w,c=wall(),cpu()
                try:
                    with transaction.atomic():order=OrderCreator().place_order(**args)
                finally:
                    elapsed_cpu,elapsed_wall=cpu()-c,wall()-w
                    if trial['profiled']:prof.disable()
                    STATE['active']=False
                events=STATE['events'][event_start:]
                with admin.cursor() as c:
                    c.execute('''SELECT toplevel,query,calls,total_plan_time,total_exec_time,rows,shared_blks_hit,shared_blks_read,wal_bytes
                         FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=%s)
                         AND userid=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY toplevel DESC,total_exec_time DESC''',
                         (trial['database'],'oscar_user'))
                    keys=[d.name for d in c.description]
                    server=[dict(zip(keys,row)) for row in c.fetchall()]
                statements.append(dict(number=number,queries=server))
                check(trial['id']+'/'+number+' server stats captured only target workload',bool(server) and all('pg_stat_statements' not in s['query'] for s in server))
                orders.append(dict(number=number,wall_ns=elapsed_wall,cpu_ns=elapsed_cpu,
                    execute_count=sum(e['kind']=='execute' for e in events),execute_wall_ns=sum(e['wall_ns'] for e in events if e['kind']=='execute'),
                    execute_cpu_ns=sum(e['cpu_ns'] for e in events if e['kind']=='execute'),commit_wall_ns=sum(e['wall_ns'] for e in events if e['kind']=='commit'),
                    server_top_plan_ms=sum(float(s['total_plan_time']) for s in server if s['toplevel']),
                    server_top_exec_ms=sum(float(s['total_exec_time']) for s in server if s['toplevel'])))
                check(trial['id']+'/'+number+' original line order and identities',list(order.lines.values_list('product_id',flat=True))==[l.product_id for l in lines] and all(l.stockrecord is s for l,s in zip(lines,stocks)))
                objects.append(base.encode({f.attname:getattr(order,f.attname) for f in order._meta.concrete_fields}))
        check(trial['id']+' physical calls unchanged', [o['execute_count'] for o in orders]==([29,53,88,17] if trial['arm']=='original' else [28,50,82,17]))
        if trial['arm']=='candidate':check(trial['id']+' candidate really executed',stats==dict(fused_updates=10,reused_reads=10,fallbacks=[],leftover_results=0))
        after=base.snapshot(connection);check(trial['id']+' entire final tables/sequences match',after==expected)
        (folder/'after-full-state.json').write_text(json.dumps(after,sort_keys=True)+'\n')
        (folder/'events.json').write_text(json.dumps(STATE['events'],indent=2)+'\n')
        (folder/'server.json').write_text(json.dumps(statements,indent=2,default=str)+'\n')
        result=dict(**trial,orders=orders,objects=objects,handoff_stats=stats)
        if trial['profiled']:
            prof.dump_stats(str(folder/'cpu.prof'))
            (folder/'cpu-profile.json').write_text(json.dumps(export_profile(prof),indent=2)+'\n')
        (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n');runs.append(result)
        print(trial['id']+' completed',flush=True)
    check('all original/candidate/profile/control order objects agree',all(r['objects']==runs[0]['objects'] for r in runs))
    # Bind all observed Python code to hashes; code IDs are process-local join keys only.
    metadata={key:code_metadata(code) for key,code in STATE['codes'].items()}
    base.save('executed-code.json',metadata)
    witnesses=[]
    def walk(code):
        yield code
        for c in code.co_consts:
            if isinstance(c,types.CodeType):yield from walk(c)
    pyc_folder=OUT/'observed-pyc';pyc_folder.mkdir()
    for name,module in sorted(sys.modules.items()):
        source=getattr(module,'__file__',None);cached=getattr(module,'__cached__',None)
        if not name.startswith(('oscar.','django.')) or not source:continue
        observed={v['code_sha256'] for v in metadata.values() if not v['native'] and v['file']==source}
        if not observed:continue
        if not cached or not Path(cached).is_file():
            witnesses.append(dict(module=name,source=source,status='no cached pyc; only live bytecode/source hash binding',source_sha256=base.digest(Path(source).read_bytes())))
            continue
        raw=Path(cached).read_bytes();available={base.code_hash(c) for c in walk(marshal.loads(raw[16:]))}
        check(name+' observed code matches pyc',observed<=available)
        (pyc_folder/(name+'.pyc')).write_bytes(raw)
        witnesses.append(dict(module=name,source=source,status='all observed code objects match cached pyc',matched_codes=len(observed),
            source_sha256=base.digest(Path(source).read_bytes()),pyc_sha256=base.digest(raw)))
    base.save('bytecode-witness.json',witnesses)
    check('both original target modules bound to pyc',all(any(w['module']==m and w['status']=='all observed code objects match cached pyc' for w in witnesses) for m in ['oscar.apps.order.utils','oscar.apps.partner.abstract_models']))
    base.save('result.json',dict(status='passed',checks=checks,runs=runs,
        limits=['deterministic CPU profile is perturbed; controls quantify combined cProfile/stack-attribution overhead',
                'thread CPU excludes server CPU; execute wall-minus-thread-CPU is not pure network or server wait',
                'server plan/execute wall times overlap driver intervals; nested statements overlap top-level function execution',
                'locally generated pyc binding, not independent upstream binary decompilation or full static reachability proof']))
    admin.close();connection.close()


if __name__=='__main__':main()
