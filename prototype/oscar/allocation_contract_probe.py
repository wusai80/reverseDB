"""Real DB contract audit only. No replacement of Oscar or Django methods."""
import json
import os
from pathlib import Path
import sys

import django
import psycopg2
from psycopg2 import sql

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
CHECKS = []


def save(name, obj):
    (OUT/name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str)+'\n')


def check(label, ok):
    assert ok, label
    CHECKS.append(label)


UPDATE = 'UPDATE partner_stockrecord SET num_allocated = COALESCE(num_allocated, 0) + %s WHERE id = %s'
READ = 'SELECT id, num_allocated FROM partner_stockrecord WHERE id = %s LIMIT 21'
FUNCTION = '''CREATE FUNCTION public.reversedb_allocate_read(p_quantity integer, p_id bigint)
RETURNS TABLE(id bigint, num_allocated integer)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, public AS $body$
BEGIN
    UPDATE public.partner_stockrecord AS s
       SET num_allocated = COALESCE(s.num_allocated, 0) + p_quantity WHERE s.id = p_id;
    RETURN QUERY SELECT s.id, s.num_allocated FROM public.partner_stockrecord AS s
                 WHERE s.id = p_id LIMIT 21;
END
$body$'''


def connect():
    return psycopg2.connect(host=os.environ['DATABASE_HOST'], port=os.environ['DATABASE_PORT'],
        dbname=os.environ['DATABASE_NAME'], user=os.environ['DATABASE_USER'], password=os.environ['DATABASE_PASSWORD'])


def snapshot(conn):
    state = dict(tables={}, sequences={})
    with conn.cursor() as c:
        c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        for (name,) in c.fetchall():
            c.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(name)))
            state['tables'][name] = [json.loads(r[0]) for r in c.fetchall()]
        c.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        for (name,) in c.fetchall():
            c.execute(sql.SQL('SELECT last_value, is_called FROM {}').format(sql.Identifier(name)))
            state['sequences'][name] = list(c.fetchone())
    return state


def main():
    django.setup()
    from django.conf import settings
    from django.db import connection, transaction
    from django.db.models.signals import pre_init, post_init, pre_save, post_save
    from oscar.core.loading import get_model
    Stock = get_model('partner', 'StockRecord')
    conn = connect(); conn.autocommit = True
    before = snapshot(conn); save('before-full-state.json', before)
    with conn.cursor() as c:
        schema = {}
        for key, query in {
            'columns': "SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_name='partner_stockrecord' ORDER BY ordinal_position",
            'triggers': "SELECT tgname,tgisinternal,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid='partner_stockrecord'::regclass ORDER BY tgname",
            'policies': "SELECT * FROM pg_policies WHERE tablename='partner_stockrecord'",
            'rules': "SELECT rulename,definition FROM pg_rules WHERE tablename='partner_stockrecord'",
            'rls': "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='partner_stockrecord'::regclass",
            'role': 'SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user',
            'isolation': 'SHOW transaction_isolation',
        }.items():
            c.execute(query); schema[key] = c.fetchall()
        c.execute(FUNCTION)
        # Public execute is not relied upon; grants are to synthetic local roles only.
        c.execute('REVOKE ALL ON FUNCTION public.reversedb_allocate_read(integer,bigint) FROM PUBLIC')
        c.execute('GRANT EXECUTE ON FUNCTION public.reversedb_allocate_read(integer,bigint) TO oscar_restricted')
        c.execute('GRANT SELECT (id,num_allocated), UPDATE (num_allocated) ON partner_stockrecord TO oscar_restricted')
    save('schema.json', schema)
    check('original fixture has no user stock trigger or rule', not schema['rules'] and all(row[1] for row in schema['triggers']))
    check('no row security policy in original fixture', schema['rls'] == [(False, False)] and not schema['policies'])
    check('test caller has no superuser or RLS bypass', schema['role'][0][1:] == (False, False))
    check('original READ COMMITTED retained', schema['isolation'] == [('read committed',)])
    save('host-bindings.json', dict(stock_class=Stock.__module__+'.'+Stock.__qualname__,
         managers=[(m.name,type(m).__module__+'.'+type(m).__qualname__) for m in Stock._meta.managers],
         default_manager=Stock._default_manager.name, base_manager=Stock._base_manager.name,
         database_routers=settings.DATABASE_ROUTERS,
         pk_type=type(Stock._meta.pk).__name__, allocated_type=type(Stock._meta.get_field('num_allocated')).__name__,
         basket_quantity_type=type(get_model('basket','Line')._meta.get_field('quantity')).__name__,
         refresh_method=Stock.refresh_from_db.__module__+'.'+Stock.refresh_from_db.__qualname__,
         from_db_method=Stock.from_db.__module__+'.'+Stock.from_db.__qualname__,
         caveat='source-bound configuration facts, not a general proof about future custom managers or callbacks'))

    # Inspect actual original host behavior; extra observers never suppress native receivers.
    originals = []
    for label, initial, missing in [('normal', 4, False), ('null', None, False), ('missing', 4, True)]:
        events = []; objects = []
        def observe_init(sender, instance, **kwargs):
            objects.append(instance)
            events.append(dict(kind='post_init', pk=instance.__dict__.get('id'), fields=sorted(instance.__dict__),
                               allocated=instance.__dict__.get('num_allocated'), same=instance is obj))
        def observe_pre(sender, instance, **kwargs):
            events.append(dict(kind='pre_save', same=instance is obj, allocated=instance.num_allocated))
        def observe_post(sender, instance, **kwargs):
            events.append(dict(kind='post_save', same=instance is obj, allocated=instance.num_allocated))
        with transaction.atomic():
            Stock.objects.filter(pk=2).update(num_allocated=initial)
            obj = Stock.objects.select_related('product__product_class', 'partner').get(pk=2)
            original_id = id(obj)
            product_alias, partner_alias = obj.product, obj.partner
            obj.can_track_allocations  # Materialize original property before observation.
            if missing:
                obj.pk = 9000000000
            post_init.connect(observe_init, sender=Stock, weak=False)
            pre_save.connect(observe_pre, sender=Stock, weak=False)
            post_save.connect(observe_post, sender=Stock, weak=False)
            queries = []
            def record(execute, statement, params, many, context):
                queries.append(dict(sql=statement, params=params, many=many))
                return execute(statement, params, many, context)
            error = None
            try:
                with connection.execute_wrapper(record):
                    obj.allocate(3)
            except Stock.DoesNotExist as exc:
                error = dict(type=type(exc).__qualname__, message=str(exc))
            finally:
                post_init.disconnect(observe_init, sender=Stock)
                pre_save.disconnect(observe_pre, sender=Stock)
                post_save.disconnect(observe_post, sender=Stock)
            check(label+' original instance and relation aliases retained', id(obj)==original_id and obj.product is product_alias and obj.partner is partner_alias)
            if missing:
                check('missing row original error and no post-save', error is not None and [e['kind'] for e in events] == ['pre_save'])
            else:
                expected = (initial or 0)+3
                check(label+' refreshed value visible to post-save', obj.num_allocated==expected and events[-1] == dict(kind='post_save', same=True, allocated=expected))
                check(label+' refresh constructs distinct partial model', any(e['kind']=='post_init' and not e['same'] and e['allocated']==expected and 'price' not in e['fields'] for e in events))
            originals.append(dict(case=label, events=events, queries=queries, error=error))
            transaction.set_rollback(True)
    save('original-host-behavior.json', originals)

    cases = [
        dict(name='ordinary', initial=4, qty=3), dict(name='null-allocation', initial=None, qty=3),
        dict(name='zero-quantity', initial=4, qty=0), dict(name='negative-stored-allocation', initial=-4, qty=3),
        dict(name='int32-overflow', initial=2147483647, qty=1), dict(name='missing-row', initial=4, qty=3, pk=9000000000),
        dict(name='before-trigger', initial=4, qty=3, trigger='before'),
        dict(name='after-trigger', initial=4, qty=3, trigger='after'),
        dict(name='skipped-update', initial=4, qty=3, trigger='skip'),
        dict(name='column-rights', initial=4, qty=3, role=True),
        dict(name='update-denied', initial=4, qty=3, role=True, deny=True),
    ]
    results = []
    for case in cases:
        outcomes = {}
        for arm in ['original-sql-pair','returning','ordered-server-pair']:
            db = connect(); db.autocommit = False
            row = dict(execute_calls=0, rows=None, error=None, lock_sqlstate=None)
            try:
                with db.cursor() as c:
                    c.execute('UPDATE partner_stockrecord SET num_allocated=%s WHERE id=2',(case['initial'],))
                    if case.get('trigger'):
                        trigger = case['trigger']
                        body = {'before': 'NEW.num_allocated := NEW.num_allocated + 10; RETURN NEW;',
                                'after': 'IF pg_trigger_depth() = 1 THEN UPDATE public.partner_stockrecord SET num_allocated=num_allocated+100 WHERE id=NEW.id; END IF; RETURN NEW;',
                                'skip': 'RETURN NULL;'}[trigger]
                        c.execute('CREATE FUNCTION public.reversedb_trigger() RETURNS trigger LANGUAGE plpgsql AS $b$ BEGIN '+body+' END $b$')
                        timing = 'AFTER' if trigger=='after' else 'BEFORE'
                        c.execute('CREATE TRIGGER reversedb_contract_trigger '+timing+' UPDATE ON partner_stockrecord FOR EACH ROW EXECUTE FUNCTION public.reversedb_trigger()')
                    if case.get('deny'):
                        c.execute('REVOKE UPDATE (num_allocated) ON partner_stockrecord FROM oscar_restricted')
                    if case.get('role'):
                        c.execute('SET LOCAL ROLE oscar_restricted')
                    # Setup acquires the target row too; an independent lock probe below is separate.
                    c.execute('SAVEPOINT operation')
                    try:
                        pk=case.get('pk',2); qty=case['qty']
                        row['execute_calls'] += 1
                        if arm == 'original-sql-pair':
                            c.execute(UPDATE,(qty,pk)); row['execute_calls'] += 1
                            c.execute(READ,(pk,))
                        elif arm == 'returning':
                            c.execute(UPDATE+' RETURNING id,num_allocated',(qty,pk))
                        else:
                            c.execute('SELECT * FROM public.reversedb_allocate_read(%s,%s)',(qty,pk))
                        row['rows'] = c.fetchall()
                    except psycopg2.Error as exc:
                        row['error'] = dict(sqlstate=exc.pgcode, primary=exc.diag.message_primary)
                        c.execute('ROLLBACK TO SAVEPOINT operation')
                    c.execute('RESET ROLE')
                    c.execute('SELECT to_jsonb(s)::text FROM partner_stockrecord s WHERE id=2')
                    row['complete_row'] = json.loads(c.fetchone()[0])
                # Compare *all* original tables, not just the affected two fields.
                row['state'] = snapshot(db)
                outcomes[arm] = row
            finally:
                db.rollback(); db.close()
        original, returning, ordered = [outcomes[a] for a in ['original-sql-pair','returning','ordered-server-pair']]
        def observable(x):
            return {k:v for k,v in x.items() if k not in ('execute_calls','lock_sqlstate')}
        check(case['name']+' ordered server pair full outcome matches', observable(original)==observable(ordered))
        if case['name'] in ['after-trigger','skipped-update']:
            check(case['name']+' rejects naive RETURNING', observable(original)!=observable(returning))
        else:
            check(case['name']+' returning control matches SQL outcome', observable(original)==observable(returning))
        save(case['name']+'-outcomes.json', outcomes)
        results.append(dict(case=case, calls={a:x['execute_calls'] for a,x in outcomes.items()},
                            rows={a:x['rows'] for a,x in outcomes.items()}, errors={a:x['error'] for a,x in outcomes.items()}))

    # No setup UPDATE on the tested row: establish the actual operation's lock and rollback lifetime.
    locks = []
    for arm in ['original-sql-pair','ordered-server-pair']:
        owner, contender = connect(), connect()
        try:
            with owner.cursor() as c:
                if arm=='original-sql-pair':
                    c.execute(UPDATE,(1,2)); c.execute(READ,(2,))
                else:
                    c.execute('SELECT * FROM public.reversedb_allocate_read(%s,%s)',(1,2))
                rows = c.fetchall()
            def try_lock():
                try:
                    with contender.cursor() as c:
                        c.execute('SELECT id FROM partner_stockrecord WHERE id=2 FOR UPDATE NOWAIT'); c.fetchall()
                    return 'acquired'
                except psycopg2.Error as exc:
                    return exc.pgcode
                finally:
                    contender.rollback()
            held = try_lock(); owner.rollback(); released = try_lock()
            check(arm+' lock held until outer rollback', held=='55P03' and released=='acquired')
            locks.append(dict(arm=arm, rows=rows, held=held, after_rollback=released))
        finally:
            owner.close(); contender.close()
    save('lock-lifetime.json', locks)
    after = snapshot(conn); save('after-full-state.json', after)
    check('entire fixture tables and sequences unchanged', before==after)
    with conn.cursor() as c:
        c.execute('DROP FUNCTION public.reversedb_allocate_read(integer,bigint)')
    conn.close(); connection.close()
    save('result.json', dict(status='passed', checks=CHECKS, cases=results,
         actual_oscar_replacement=False, performance_claim=None,
         decision='Reject naive RETURNING. Ordered invoker UPDATE/SELECT is a tested DB-side building block only.',
         outstanding=['original Django materialization/refresh must consume returned rows without losing pre/post-init, deferred fields, caches or missing-row exception',
                      'no complete Python runtime backend or application-level equivalence yet',
                      'router/manager/custom cursor changes and concurrent missing-row insertion not validated',
                      'SQL error code/primary compared; full exception diagnostics and extra EXECUTE privilege still require host contract'],
         host_scope='Original allocate observed with added nonmutating signal listeners; original callbacks retained.',
         sql_scope='Both original ordered SQL statements from the existing boundary; SQL arms do not run Python callbacks. All fixture data retained.'))


if __name__ == '__main__':
    main()
