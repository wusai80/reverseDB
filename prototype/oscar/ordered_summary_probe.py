"""Bounded SQL mechanism: ordered count + scalar reduction, never UPDATE fusion.

Original full-row read / Python integer sum is the reference. Existing local
contracts supply the materialization-purity obligation, not this SQL probe.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import psycopg2
import baseline_probe as base

BINDING=json.loads(Path(os.environ['RDB_ANALYTICS_BINDING']).read_text())
READ=BINDING['read_sql']
COUNT=BINDING['count_sql']
GATE=8437726108


def connect():
    return psycopg2.connect(host=os.environ['DATABASE_HOST'],port=os.environ['DATABASE_PORT'],
        dbname=os.environ['DATABASE_NAME'],user=os.environ['DATABASE_USER'],password=os.environ['DATABASE_PASSWORD'])


def main():
    import django
    django.setup()
    from django.db import connection
    from oscar.core.loading import get_model
    Line=get_model('order','Line')
    quantity_index=list(Line._meta.concrete_fields).index(Line._meta.get_field('quantity'))
    checks=[]
    def check(label,ok):
        assert ok,label
        checks.append(label)
    def state(db):return base.snapshot(SimpleNamespace(cursor=db.cursor,ops=connection.ops))
    initial=base.snapshot(connection)
    check('complete committed original baseline',initial==json.loads(Path(os.environ['RDB_EXPECTED_STATE']).read_text()))
    with connection.cursor() as c:
        c.execute("SELECT attnotnull,atttypid::regtype::text FROM pg_attribute WHERE attrelid='order_line'::regclass AND attname='quantity'")
        check('quantity actual database domain is nonnull int32',c.fetchone()==(True,'integer'))
        c.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        check('original caller has no superuser or bypass privilege',c.fetchone()==(False,False))
    count_sql=COUNT.replace(' AS "__count"','').replace('FROM "order_line"','FROM public."order_line"').replace('%s','p_order_id')
    read_sql=READ.replace('FROM "order_line"','FROM public."order_line"').replace('%s','p_order_id')
    definitions={}
    for name,volatility,gate in [('reversedb_ordered_summary','VOLATILE',False),
                                 ('reversedb_ordered_summary_gate','VOLATILE',True),
                                 ('reversedb_stable_summary_gate','STABLE',True)]:
        definitions[name]=(f'CREATE FUNCTION public.{name}(p_order_id bigint) RETURNS TABLE(n bigint,q numeric) '
            f'LANGUAGE plpgsql {volatility} SECURITY INVOKER SET search_path=pg_catalog,public AS $body$ BEGIN '
            +count_sql.replace('SELECT COUNT(*)','SELECT COUNT(*) INTO n')+'; '
            +(f'PERFORM pg_advisory_xact_lock({GATE}::bigint); ' if gate else '')
            +'SELECT COALESCE(SUM(r."quantity"::numeric),0) INTO q FROM ('+read_sql+') AS r; RETURN NEXT; END $body$')
    with connection.cursor() as c:
        for name,sql in definitions.items():
            c.execute(sql);c.execute(f'REVOKE ALL ON FUNCTION public.{name}(bigint) FROM PUBLIC')
            c.execute(f'GRANT EXECUTE ON FUNCTION public.{name}(bigint) TO oscar_packet_reader')
        c.execute('GRANT SELECT ON order_line TO oscar_packet_reader')
        c.execute("SELECT proname,provolatile,prosecdef FROM pg_proc WHERE proname LIKE 'reversedb_%summary%' ORDER BY proname")
        functions=c.fetchall()
    base.save('functions.json',dict(definitions=definitions,catalog=functions,
        test_hook='Gate variants differ only by a test advisory lock between the two SELECT statements; never installed in application'))
    external=connect();external.autocommit=True
    quantities={r['id']:r['quantity'] for r in initial['tables']['order_line'] if r['order_id']==3}
    cases=['ordinary','empty','bag','wide-sum','committed-update-between-reads','committed-insert-between-reads',
           'repeatable-read-control','fresh-after-zero-update','rls-invoker','projection-denied','count-denied']
    results=[]
    try:
        for label in cases:
            arms=['original','ordered']
            if label in ['committed-update-between-reads','committed-insert-between-reads']:arms.append('stable-negative-control')
            for arm in arms:
                db=connect();db.set_session(isolation_level='REPEATABLE READ' if label=='repeatable-read-control' else 'READ COMMITTED')
                row=dict(case=label,arm=arm,pairs=[],query_calls=0,error=None,gate_observed=False)
                interleaved=label in ['committed-update-between-reads','committed-insert-between-reads','repeatable-read-control']
                def pair(gated=False):
                    with db.cursor() as c:
                        if arm=='original':
                            row['query_calls']+=1;c.execute(COUNT,[900001 if label=='empty' else 3]);n=c.fetchone()[0]
                            if gated:c.execute('SELECT pg_advisory_xact_lock(%s)',[GATE])
                            row['query_calls']+=1;c.execute(READ,[900001 if label=='empty' else 3]);records=c.fetchall()
                            return [n,sum(r[quantity_index] for r in records)]
                        name=('reversedb_stable_summary_gate' if arm=='stable-negative-control' else
                              'reversedb_ordered_summary_gate' if gated else 'reversedb_ordered_summary')
                        row['query_calls']+=1;c.execute('SELECT * FROM public.'+name+'(%s)',[900001 if label=='empty' else 3]);n,q=c.fetchone()
                        check(label+'/'+arm+' numeric bridge exact',q==int(q))
                        return [n,int(q)]
                try:
                    with db.cursor() as c:
                        c.execute("SET LOCAL statement_timeout='10s'")
                        # DDL precedes fixture DML: deferred FK checks from the
                        # latter must not be disabled just to ALTER the table.
                        if label=='rls-invoker':
                            c.execute('ALTER TABLE order_line ENABLE ROW LEVEL SECURITY')
                            c.execute('CREATE POLICY packet_filter ON order_line FOR SELECT TO oscar_packet_reader USING (quantity>0)')
                        if label in ['bag','wide-sum','rls-invoker']:
                            c.execute('UPDATE order_line SET quantity=0 WHERE order_id=3')
                            c.execute('UPDATE order_line SET quantity=%s,product_id=1 WHERE id=ANY(%s)',[2**31-1 if label=='wide-sum' else 2,sorted(quantities)[:2]])
                        if label=='fresh-after-zero-update':
                            c.execute('CREATE FUNCTION public.reversedb_summary_miss() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE public.order_line SET quantity=quantity+1 WHERE order_id=3; RETURN NULL; END $$')
                            c.execute('CREATE TRIGGER summary_miss AFTER UPDATE ON analytics_userrecord FOR EACH STATEMENT EXECUTE FUNCTION public.reversedb_summary_miss()')
                        if label in ['projection-denied','count-denied']:
                            c.execute('REVOKE SELECT ON order_line FROM oscar_packet_reader')
                            if label=='projection-denied':c.execute('GRANT SELECT(order_id,quantity) ON order_line TO oscar_packet_reader')
                        if label in ['rls-invoker','projection-denied','count-denied']:c.execute('SET LOCAL ROLE oscar_packet_reader')
                        c.execute('SAVEPOINT operation')
                    try:
                        if interleaved:
                            with external.cursor() as c:c.execute('SELECT pg_advisory_lock(%s)',[GATE])
                            with ThreadPoolExecutor(max_workers=1) as executor:
                                future=executor.submit(pair,True)
                                try:
                                    for _ in range(250):
                                        with external.cursor() as c:
                                            c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)",[db.get_backend_pid()]);blocked=c.fetchone()[0]
                                        if blocked:row['gate_observed']=True;break
                                        if future.done():future.result();raise AssertionError('Read passed gate unexpectedly')
                                        time.sleep(.01)
                                    check(label+'/'+arm+' actual between-statement gate',row['gate_observed'])
                                    with external.cursor() as c:
                                        if label=='committed-insert-between-reads':
                                            columns=[f.column for f in Line._meta.concrete_fields];quoted=[connection.ops.quote_name(n) for n in columns]
                                            expr=['900009' if n=='id' else connection.ops.quote_name(n) for n in columns]
                                            c.execute('INSERT INTO order_line ('+','.join(quoted)+') SELECT '+','.join(expr)+' FROM order_line WHERE id=%s',[min(quantities)])
                                        else:c.execute('UPDATE order_line SET quantity=quantity+1 WHERE order_id=3')
                                finally:
                                    with external.cursor() as c:c.execute('SELECT pg_advisory_unlock(%s)',[GATE])
                                row['pairs'].append(future.result(timeout=12))
                            base.save(label+'-'+arm+'-external-state.json',state(external))
                        else:row['pairs'].append(pair())
                        if label=='fresh-after-zero-update':
                            with db.cursor() as c:
                                c.execute('UPDATE analytics_userrecord SET num_orders=num_orders+1 WHERE user_id=999999')
                                row['update_affected']=c.rowcount
                            row['pairs'].append(pair())
                    except psycopg2.Error as exc:
                        row['error']=dict(type=type(exc).__name__,sqlstate=exc.pgcode,message=str(exc),primary=exc.diag.message_primary,
                            context=exc.diag.context,internal_query=exc.diag.internal_query,internal_position=exc.diag.internal_position,
                            statement_position=exc.diag.statement_position)
                        with db.cursor() as c:c.execute('ROLLBACK TO SAVEPOINT operation')
                    with db.cursor() as c:c.execute('RESET ROLE')
                    base.save(label+'-'+arm+'-full-state.json',state(db))
                    results.append(row);base.save('cases.json',results)
                finally:
                    db.rollback();db.close()
                    if interleaved:
                        with external.cursor() as c:
                            if label=='committed-insert-between-reads':c.execute('DELETE FROM order_line WHERE id=900009')
                            c.executemany('UPDATE order_line SET quantity=%s WHERE id=%s',[(q,pk) for pk,q in quantities.items()])
                    check(label+'/'+arm+' all data and sequences restored',base.snapshot(connection)==initial)
        diagnostics=[]
        for label in cases:
            a=next(r for r in results if r['case']==label and r['arm']=='original');b=next(r for r in results if r['case']==label and r['arm']=='ordered')
            check(label+' scalar values equal',a['pairs']==b['pairs'])
            check(label+' complete state equal',json.loads((base.OUT/(label+'-original-full-state.json')).read_text())==json.loads((base.OUT/(label+'-ordered-full-state.json')).read_text()))
            if a['error']:
                check(label+' error type SQLSTATE and primary message preserved',all(a['error'][k]==b['error'][k] for k in ['type','sqlstate','primary']))
                check(label+' added server diagnostic retained',a['error']['message']!=b['error']['message'] and b['error']['context'] is not None)
                diagnostics.append(dict(case=label,original=a['error'],ordered=b['error']))
            else:
                check(label+' no new error',b['error'] is None)
                check(label+' read calls halved',a['query_calls']==2*b['query_calls'])
        def result(label,arm='ordered'):return next(r for r in results if r['case']==label and r['arm']==arm)
        for label,expected in [('ordinary',[6,6]),('empty',[0,0]),('bag',[6,4]),('wide-sum',[6,4294967294]),
            ('committed-update-between-reads',[6,12]),('committed-insert-between-reads',[6,7]),('repeatable-read-control',[6,6]),('rls-invoker',[2,4])]:
            check(label+' expected result',result(label)['pairs']==[expected])
        for label in ['committed-update-between-reads','committed-insert-between-reads']:
            check(label+' STABLE negative control rejected',result(label,'stable-negative-control')['pairs']==[[6,6]] and result(label)['pairs']!=[[6,6]])
        check('miss continuation needs a fresh pair',result('fresh-after-zero-update')['pairs']==[[6,6],[6,12]] and result('fresh-after-zero-update')['update_affected']==0)
        base.save('diagnostic-differences.json',diagnostics)
        base.save('result.json',dict(status='passed',checks=checks,cases=len(cases),arm_runs=len(results),
            conclusion='Ordered VOLATILE read mechanism retains tested statement snapshots; full error diagnostics differ',
            admission=False,application_replacement=False,performance_claim=None,
            limits=['SQL mechanism only; does not validate a new Python property handoff or whole receiver rewrite',
                    'Materialization purity and model/manager guards still required; earlier first-read contract does not automatically admit miss branch',
                    'Test-only lock gates establish ordering; read committed is not silently changed',
                    'Full errors retained; no retry of failed SQL and no suppression of CONTEXT differences']))
    finally:external.close();connection.close()


if __name__=='__main__':main()
