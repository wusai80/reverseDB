"""Fixed interleavings, no throughput measurement and no production admission."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

import psycopg2.extensions
import baseline_probe as base
import allocation_handoff as handoff

OUT=Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
BINDING=json.loads(Path(os.environ['RDB_HANDOFF_BINDING']).read_text())
LOCAL=threading.local()
CHECKS=[]


def check(label,ok):
    assert ok,label
    CHECKS.append(label)


class Cursor(psycopg2.extensions.cursor):
    def execute(self,query,vars=None):
        if getattr(LOCAL,'active',False):
            LOCAL.events.append(dict(sql=str(query),params=base.encode(vars)))
        return super().execute(query,vars)


def wait(event):
    if not event.wait(10):raise TimeoutError('fixed interleaving event')


def join(threads):
    for t in threads:t.join(12)
    assert not any(t.is_alive() for t in threads),'worker deadline exceeded'


def lifecycle(module):
    """A enters, B enters, A exits, B still active, B exits."""
    from django.db.models.sql.compiler import SQLCompiler,SQLUpdateCompiler
    original=(SQLCompiler.execute_sql,SQLUpdateCompiler.execute_sql)
    entered_a,entered_b,exited_a=[threading.Event() for _ in range(3)]
    result={};errors=[]
    def a():
        try:
            with module.installed(BINDING):entered_a.set();wait(entered_b)
        except BaseException as e:errors.append(repr(e))
        finally:exited_a.set()
    def b():
        try:
            wait(entered_a)
            with module.installed(BINDING):
                entered_b.set();wait(exited_a)
                result['hooks_active_while_b_active']=(SQLCompiler.execute_sql,SQLUpdateCompiler.execute_sql)!=original
        except BaseException as e:errors.append(repr(e))
    ts=[threading.Thread(target=a),threading.Thread(target=b)]
    try:
        for t in ts:t.start()
        join(ts)
        result['restored_after_b_exit']=(SQLCompiler.execute_sql,SQLUpdateCompiler.execute_sql)==original
        result['errors']=errors
    finally:
        # Historical negative control is deliberately confined to this probe.
        SQLCompiler.execute_sql,SQLUpdateCompiler.execute_sql=original
    return result


def main():
    import django
    from django.conf import settings
    settings.DATABASES['default']['OPTIONS']=dict(cursor_factory=Cursor)
    django.setup()
    from django.db import connection,connections,transaction
    from django.db.models.query import QuerySet
    from oscar.core.loading import get_model
    Stock=get_model('partner','StockRecord')
    check('actual allocate bytecode bound',base.code_hash(Stock.allocate.__code__)==BINDING['allocate_code_hash'])
    before=base.snapshot(connection);base.save('before-full-state.json',before)
    spec=importlib.util.spec_from_file_location('historical_handoff',os.environ['RDB_OLD_HANDOFF'])
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    negative=lifecycle(old);fixed=lifecycle(handoff)
    check('historical overlapping-context defect reproduced',negative==dict(hooks_active_while_b_active=False,restored_after_b_exit=False,errors=[]))
    check('fixed overlapping contexts share hooks and restore last',fixed==dict(hooks_active_while_b_active=True,restored_after_b_exit=True,errors=[]))
    base.save('installation-lifecycle.json',dict(historical=negative,fixed=fixed))

    with connection.cursor() as c:
        c.execute("SELECT to_regprocedure('public.reversedb_allocate_packet(integer,bigint)')")
        absent=c.fetchone()[0] is None
        c.execute(handoff.PACKET_SQL)
        c.execute('REVOKE ALL ON FUNCTION public.reversedb_allocate_packet(integer,bigint) FROM PUBLIC')
        c.execute("SELECT p.prosecdef,p.provolatile,p.proconfig,has_function_privilege(current_user,p.oid,'EXECUTE'),has_function_privilege('oscar_noexecute',p.oid,'EXECUTE'),pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.oid='public.reversedb_allocate_packet(integer,bigint)'::regprocedure")
        fn=c.fetchone()
        c.execute("SELECT 'partner_stockrecord'::regclass::oid='public.partner_stockrecord'::regclass::oid,current_schemas(true)")
        resolution=c.fetchone()
        c.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user");role=c.fetchone()
        c.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.partner_stockrecord'::regclass");rls=c.fetchone()
        c.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='public.partner_stockrecord'::regclass AND NOT tgisinternal");triggers=c.fetchone()[0]
    check('missing provision is a failed precondition',absent)
    check('exact local role can execute invoker volatile function',fn[0:2]==(False,'v') and fn[3] and fn[2]==['search_path=pg_catalog, public'])
    check('other role without execute is not eligible',not fn[4])
    check('frozen relation resolution and no user trigger/RLS',resolution[0] and rls==(False,False) and triggers==0)
    check('application role has no superuser/RLS bypass',role==(False,False))
    base.save('preconditions.json',dict(missing_before_provision=absent,function=fn,relation_resolution=resolution,role=role,rls=rls,user_triggers=triggers,
        boundary='read-only admission audit of this frozen fixture; not a hot-reload/schema-drift guard'))

    def blocked(pid,blocker):
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            with connection.cursor() as c:
                c.execute('SELECT pg_blocking_pids(%s)',(pid,))
                if blocker in c.fetchone()[0]:return True
            time.sleep(.01)
        return False

    def context(arm):return handoff.installed(BINDING) if arm=='candidate' else nullcontext({})
    def load_stock(pk):
        obj=Stock.objects.select_related('product__product_class','partner').get(pk=pk)
        obj.can_track_allocations
        return obj
    def allocate(obj,quantity):
        product,partner=obj.product,obj.partner
        LOCAL.events=[];LOCAL.active=True
        try:obj.allocate(quantity)
        finally:LOCAL.active=False
        return dict(pk=obj.pk,value=obj.num_allocated,aliases_retained=obj.product is product and obj.partner is partner,
                    calls=list(LOCAL.events),backend_pid=connections['default'].connection.get_backend_pid())
    results=[]
    for case in ['overlapping-distinct-rows','same-row-writer-wait','missing-row-insert']:
        arms={}
        for arm in ['original','candidate']:
            errors=[];observations={};stats={};pids={}
            entered_a,entered_b,exited_a,release,started_b=[threading.Event() for _ in range(5)]
            ready=threading.Barrier(2)
            pending_at_barrier={}
            def worker(label,pk,quantity):
                try:
                    with context(arm) as s:
                        stats[label]=s
                        with transaction.atomic():
                            obj=load_stock(pk);pids[label]=connections['default'].connection.get_backend_pid()
                            if case=='overlapping-distinct-rows':
                                if label=='a':entered_a.set()
                                else:wait(entered_a);entered_b.set()
                                # Pause both at return from original QuerySet.update,
                                # after each candidate packet exists, before refresh.
                                def pause_before_refresh(frame,event,arg):
                                    if event=='return' and frame.f_code is QuerySet.update.__code__ and frame.f_back and frame.f_back.f_code is Stock.allocate.__code__:
                                        state=handoff.STATE.get()
                                        pending=state and state['pending']
                                        pending_at_barrier[label]=dict(pk=pending['pk'],rows=pending['rows']) if pending else None
                                        ready.wait(10)
                                sys.setprofile(pause_before_refresh)
                                try:observations[label]=allocate(obj,quantity)
                                finally:sys.setprofile(None)
                                if label=='a':wait(entered_b)
                                else:wait(exited_a)
                            else:
                                ready.wait(10)
                                if label=='b':wait(entered_a);entered_b.set();started_b.set()
                                observations[label]=allocate(obj,quantity)
                                if label=='a':entered_a.set();wait(release)
                except BaseException as e:errors.append(dict(worker=label,error=repr(e)))
                finally:
                    if label=='a':exited_a.set()
                    connections.close_all()

            if case!='missing-row-insert':
                ts=[threading.Thread(target=worker,args=('a',2,2 if case=='overlapping-distinct-rows' else 1)),
                    threading.Thread(target=worker,args=('b',3 if case=='overlapping-distinct-rows' else 2,5 if case=='overlapping-distinct-rows' else 2))]
                for t in ts:t.start()
                did_block=None
                try:
                    if case=='same-row-writer-wait':
                        wait(started_b);did_block=blocked(pids['b'],pids['a'])
                finally:release.set()
                join(ts)
                if case=='same-row-writer-wait':check(arm+' competing writer actually blocked',did_block)
                expected={'a':2,'b':5} if case=='overlapping-distinct-rows' else {'a':1,'b':3}
                check(arm+' '+case+' exact per-worker results',not errors and {k:v['value'] for k,v in observations.items()}==expected)
            else:
                # AFTER STATEMENT fires on a zero-row UPDATE. Its advisory-lock
                # wait lets another connection commit INSERT before the SELECT.
                # The function under test is unchanged, including VOLATILE.
                loaded,start=[threading.Event() for _ in range(2)]
                def missing_worker():
                    try:
                        obj=load_stock(8);pids['a']=connections['default'].connection.get_backend_pid();loaded.set();wait(start)
                        with context(arm) as s:
                            stats['a']=s
                            with transaction.atomic():observations['a']=allocate(obj,3)
                    except BaseException as e:errors.append(dict(worker='a',error=repr(e)))
                    finally:connections.close_all()
                t=threading.Thread(target=missing_worker);t.start();wait(loaded)
                with connection.cursor() as c:
                    c.execute('SELECT pg_backend_pid()');blocker=c.fetchone()[0]
                    c.execute('DELETE FROM partner_stockrecord WHERE id=8')
                    c.execute('CREATE FUNCTION public.reversedb_wait_insert() RETURNS trigger LANGUAGE plpgsql AS $b$ BEGIN PERFORM pg_advisory_xact_lock(77114008); RETURN NULL; END $b$')
                    c.execute('CREATE TRIGGER reversedb_wait_insert AFTER UPDATE ON partner_stockrecord FOR EACH STATEMENT EXECUTE FUNCTION public.reversedb_wait_insert()')
                    c.execute('SELECT pg_advisory_lock(77114008)')
                start.set()
                try:
                    did_block=blocked(pids['a'],blocker)
                    row=dict(next(r for r in before['tables']['partner_stockrecord'] if r['id']==8));row['num_allocated']=7
                    with connection.cursor() as c:
                        c.execute('INSERT INTO partner_stockrecord SELECT * FROM json_populate_record(NULL::partner_stockrecord,%s::json)',(json.dumps(row),))
                finally:
                    with connection.cursor() as c:c.execute('SELECT pg_advisory_unlock(77114008)')
                join([t])
                with connection.cursor() as c:
                    c.execute('DROP TRIGGER reversedb_wait_insert ON partner_stockrecord');c.execute('DROP FUNCTION public.reversedb_wait_insert()')
                check(arm+' missing-row update paused before later read',did_block)
                check(arm+' subsequent SELECT sees concurrent committed insertion',not errors and observations['a']['value']==7)
            check(arm+' '+case+' all aliases retained',all(v['aliases_retained'] for v in observations.values()))
            if arm=='candidate':
                if case=='overlapping-distinct-rows':
                    check('two distinct packets simultaneously pending before refresh',pending_at_barrier==dict(a=dict(pk=2,rows=[(2,2)]),b=dict(pk=3,rows=[(3,5)])))
                check(case+' each thread used its own candidate packet',all(s==dict(fused_updates=1,reused_reads=1,fallbacks=[],leftover_results=0) for s in stats.values()))
                check(case+' actual function call in every thread',all(sum(e['sql']==handoff.CALL for e in v['calls'])==1 for v in observations.values()))
                check(case+' process compiler hooks released',handoff.ACTIVE_INSTALL is None)
            else:
                check(case+' original UPDATE and refresh both executed',all(sum(e['sql'] in [BINDING['update_sql'],BINDING['read_sql']] for e in v['calls'])==2 for v in observations.values()))
            state=base.snapshot(connection);base.save(case+'-'+arm+'-full-state.json',state)
            arms[arm]=dict(workers=observations,stats=stats,errors=errors,actual_wait=did_block,pending_at_barrier=pending_at_barrier)
            # Restore exact modified fixture cells; no rows or sequences omitted.
            with connection.cursor() as c:
                for pk in [2,3,8]:
                    original=next(r for r in before['tables']['partner_stockrecord'] if r['id']==pk)
                    c.execute('UPDATE partner_stockrecord SET num_allocated=%s WHERE id=%s',(original['num_allocated'],pk))
            check(arm+' '+case+' fixture fully restored',base.snapshot(connection)==before)
        a=arms['original'];b=arms['candidate']
        observable=lambda x:{k:{field:value for field,value in v.items() if field not in ['calls','backend_pid']} for k,v in x['workers'].items()}
        check(case+' original/candidate business observations match',observable(a)==observable(b))
        check(case+' original/candidate complete tables and sequences match',json.loads((OUT/(case+'-original-full-state.json')).read_text())==json.loads((OUT/(case+'-candidate-full-state.json')).read_text()))
        base.save(case+'.json',arms);results.append(case)
    after=base.snapshot(connection);base.save('after-full-state.json',after)
    check('complete fixture preserved at end',before==after)
    base.save('result.json',dict(status='passed',checks=CHECKS,cases=results,
        fixed_schedule=True,performance_claim=None,
        limitations=['three controlled interleavings, not arbitrary schedule proof or throughput',
                     'error CONTEXT/log difference remains; no full-observable equivalence admission',
                     'no complete HTTP payment/checkout transaction verification',
                     'schema/role preflight is evidence for frozen configuration, not dynamic drift enforcement']))
    connection.close()


if __name__=='__main__':main()
