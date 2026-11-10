#!/usr/bin/env python3
"""Bounded common access-summary test; no export rerun or backend admission."""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from fusion_regions import sql_effect
from region_contracts import fingerprint,select_existing_binding
from sql_effect_observations import legacy_observation,sqlite_observation,attach_observations

ROOT=Path(__file__).resolve().parents[1]
COMMON=ROOT/'evidence/common-region-contracts/20261109-6c096c6ddff2'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
BEETS=ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'


def main():
    out=ROOT/'evidence/sql-effect-observations'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(n,x):(out/n).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
    checks=[]
    def check(n,ok):
        assert ok,n
        checks.append(n)
    schedule=['nested_in','derived_group_sort','join','correlated','view','cte','count_star','literal_decoy',
              'renamed_tables','unknown_function','custom_function','override_builtin','pragma','write_cte',
              'multiple_statements','missing_table','virtual_schema']
    save('protocol.json',dict(cases=['all original NewOrder SQL','all original Payment SQL','two actual beets queries'],
        structural_controls=schedule,source='SQLite stable authorizer API for dependencies; EXPLAIN output is diagnostic only',
        scope='Compile-time direct relation observations, not effects closure, equivalence or query-removal permission',
        no_business_query_execution=True,no_application_data_writes=True,no_performance_samples=True,
        no_sql_rewriting=True,no_selector_change=True))
    for f in ['sql_effect_observations.py','test_sql_effect_observations.py']:
        shutil.copy2(ROOT/'scripts'/f,out/f)
    save('official-sources.json',dict(authorizer='https://www.sqlite.org/c3ref/set_authorizer.html',
        explain='https://www.sqlite.org/lang_explain.html',virtual_tables='https://www.sqlite.org/vtab.html',
        finding='EXPLAIN does not suppress prepare-time effects. Existing virtual schemas are declined; no arbitrary SQL safety or application-hook fidelity is claimed. No opcode format used for admission.'))
    try:
        check('common input artifacts intact',all(fingerprint(COMMON/p)==h for p,h in load(COMMON/'manifest.json').items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' original core unchanged',fingerprint(ROOT/'scripts'/f)==fingerprint(FIRST/'inputs/scripts'/f))
        database=BEETS/'default/original/library.db';before=fingerprint(database)
        cases=load(BEETS/'cases.json');ordinary=next(c for c in cases if c['case']=='default')['arms'][0]['queries']
        descriptions={};counts={}
        for name in ['neworder','payment','beets']:
            d=load(COMMON/(name+'-description.json'));observations={}
            for unit in d['program']['units']:
                rows=[]
                for q in unit.get('queries',[]):
                    if name=='beets':
                        bound=next(x for x in ordinary if x['sql']==q)
                        row=sqlite_observation(q,database,bound['params'])
                    else:
                        row=legacy_observation(q)
                        check(name+'/'+unit['id']+' exact legacy SQL summary',row['legacy_effect']==sql_effect(q))
                    rows.append(row)
                observations[unit['id']]=rows
            attached=attach_observations(d,observations)
            check(name+' original program, scope and unknowns not changed',
                  all(attached[k]==d[k] for k in d))
            result=select_existing_binding(attached,ROOT)
            check(name+' original selection/unbound result retained',result==load(COMMON/(name+'-decision.json')))
            if name=='beets':
                a,b=observations['fixed_read'][0],observations['flex_read'][0]
                check('beets fixed query reads items',a['direct_access']['reads']==['items'])
                check('beets flex query independently reads both tables',b['direct_access']['reads']==['item_attributes','items'])
                check('beets compilation observes functions without purity claim',
                      {x['name'] for x in b['functions']}=={'coalesce','lower','nullif','typeof'} and not b['complete_effects'])
                check('beets original selector SQL grammar remains unknown',all(not sql_effect(q['sql'])['known'] for q in ordinary))
            counts[name]=sum(map(len,observations.values()))
            save(name+'-observations.json',observations);save(name+'-description.json',attached);save(name+'-decision.json',result)
            descriptions[name]=attached
        check('application database byte-identical after compilation',fingerprint(database)==before)

        # Generic fixture, unrelated names and no rows: compile facts must not
        # depend on a populated dataset. All setup is separate from the app DB.
        fixture=out/'generic.db'
        with sqlite3.connect(fixture) as c:
            c.executescript('CREATE TABLE alpha(id INTEGER, label TEXT); CREATE TABLE beta(entity_id INTEGER,value TEXT); '
                'CREATE TABLE warehouse_x(id INTEGER,label TEXT); CREATE TABLE extra_y(entity_id INTEGER,value TEXT); '
                'CREATE VIEW joined_view AS SELECT a.id,b.value FROM alpha a JOIN beta b ON a.id=b.entity_id;')
        fixtures_hash=fingerprint(fixture)
        queries={
            'nested_in':('SELECT * FROM beta WHERE entity_id IN (SELECT id FROM (SELECT * FROM alpha WHERE id=? GROUP BY id))',(1,),['alpha','beta']),
            'derived_group_sort':("SELECT x.* FROM (SELECT * FROM alpha GROUP BY id) x ORDER BY coalesce(label,'') COLLATE NOCASE",(),['alpha']),
            'join':('SELECT a.id,b.value FROM alpha a JOIN beta b ON a.id=b.entity_id',(),['alpha','beta']),
            'correlated':('SELECT a.id FROM alpha a WHERE EXISTS (SELECT 1 FROM beta b WHERE b.entity_id=a.id)',(),['alpha','beta']),
            'view':('SELECT * FROM joined_view',(),['alpha','beta','joined_view']),
            'cte':('WITH q AS (SELECT id FROM alpha) SELECT * FROM beta WHERE entity_id IN (SELECT id FROM q)',(),['alpha','beta']),
            'count_star':('SELECT count(*) FROM alpha',(),['alpha']),
            'literal_decoy':("SELECT 'FROM absent_table' AS text FROM alpha -- JOIN missing_table\n",(),['alpha']),
            'renamed_tables':('SELECT * FROM extra_y WHERE entity_id IN (SELECT id FROM (SELECT * FROM warehouse_x WHERE id=? GROUP BY id))',(1,),['extra_y','warehouse_x']),
        }
        witnesses={};callback_calls=[]
        for name,(q,params,expected) in queries.items():
            result=sqlite_observation(q,fixture,params);witnesses[name]=result
            # SQLite can omit the database name when no table column is read.
            # Keep that uncertainty instead of manufacturing a main-db binding.
            expected_status='unknown_relation_access' if name in ['count_star','literal_decoy'] else 'compiled_access_observation'
            check(name+' exact expected relation set and certainty',result['status']==expected_status and result['direct_access']['reads']==expected)
        check('count-star table retained despite no column projection',any(e['action']==sqlite3.SQLITE_READ and e['arg1']=='alpha' and e['arg2']=='' for e in witnesses['count_star']['authorizer_events']))
        check('missing database identity stays unknown',all(witnesses[n]['unknown_relations'] and
              witnesses[n]['unknown_relations'][0]['database'] is None for n in ['count_star','literal_decoy']))
        for name,q in [('unknown_function','SELECT absent_function(id) FROM alpha'),
                       ('pragma','PRAGMA user_version=9'),('write_cte','WITH q AS (SELECT 1) DELETE FROM alpha'),
                       ('multiple_statements','SELECT * FROM alpha; DELETE FROM beta'),('missing_table','SELECT * FROM absent_table')]:
            result=sqlite_observation(q,fixture);witnesses[name]=result
            check(name+' remains unsupported/unknown',result['status'] in ['compile_error','unsupported_statement'] and not result['authorizes_replacement'])
        def sentinel(value):
            callback_calls.append(value)
            raise RuntimeError('This function must not execute during analysis')
        for name,fn in [('custom_function','effect_fn'),('override_builtin','lower')]:
            # A scalar SELECT with no FROM would call the sentinel even on an
            # empty fixture if the business statement were actually executed.
            result=sqlite_observation(f"SELECT {fn}('sentinel')",fixture,test_functions=[(fn,1,sentinel)])
            witnesses[name]=result
            check(name+' registered callback remains unknown',result['status']=='compiled_access_observation' and
                  any(any(v[1]==0 for v in f['registered_variants']) for f in result['functions']) and not result['complete_effects'])
        check('no callback bodies executed',callback_calls==[])
        virtual=out/'virtual.db'
        with sqlite3.connect(virtual) as c:c.execute('CREATE VIRTUAL TABLE index_table USING fts5(content)')
        result=sqlite_observation('SELECT * FROM index_table',virtual);witnesses['virtual_schema']=result
        check('virtual schema declined before supplied SQL compilation',result['status']=='unsupported_schema' and result['authorizer_events']==[])
        check('complete fixed control schedule',set(witnesses)==set(schedule))
        check('generic fixture byte-identical',fingerprint(fixture)==fixtures_hash)
        save('generic-controls.json',witnesses)
        # Attachment is evidence only; wrong SQL, coverage and claimed purity
        # cannot silently modify the common source contract.
        base=load(COMMON/'beets-description.json')
        obs=load(out/'beets-observations.json')
        negatives=[]
        for kind in ['wrong_sql','missing_unit','false_admission']:
            changed=copy.deepcopy(obs)
            if kind=='wrong_sql':changed['fixed_read'][0]['sql_sha256']='0'*64
            elif kind=='missing_unit':changed.pop('scalar_consumption')
            else:changed['fixed_read'][0]['authorizes_replacement']=True
            try:attach_observations(base,changed)
            except ValueError as exc:negatives.append(dict(case=kind,error=str(exc)))
        check('all three misleading attachments rejected',len(negatives)==3)
        save('negative-attachments.json',negatives)
        save('report.json',dict(status='passed',checks=checks,query_counts=counts,controls=len(witnesses),
            callbacks_executed=len(callback_calls),business_statements_executed=0,selector_modified=False,
            beets_backend_admitted=False,
            conclusion='Native compile evidence recovers nested table reads; total effects remain unresolved. Original plans and beets unbound decision unchanged.',
            limits='Source/schema/version-bound analysis in owned connections; no general SQL sandbox, application-hook attestation, purity proof or independent decompilation'))
        inputs=[database,COMMON/'manifest.json',BEETS/'cases.json']
        save('input-hashes.json',{str(p.relative_to(ROOT)):fingerprint(p) for p in inputs})
    except BaseException as exc:
        save('failure.json',dict(error=type(exc).__name__,message=str(exc),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
