#!/usr/bin/env python3
"""Fixed lineage/DAG pilot consuming freshly archived attributed AST facts."""
import copy
import json
import shutil
import uuid
from pathlib import Path
from app_db_smoke import ROOT, digest
from cross_layer_dependencies import recover, correspond, condense, functions

FACTS=ROOT/'evidence/jdbc-boundary-contracts/20261105-a7de7fb82bc7'
E2E=ROOT/'evidence/fusion-e2e/20261105-3516c48063a9'


def main():
    out=ROOT/'evidence/cross-layer-dependencies'/('20261105-'+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={})
    def save(name,obj): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok: raise AssertionError(name)
        report['checks'].append(name)
    def freeze(path):
        dest=out/'inputs'/path.relative_to(ROOT); dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dest)
        report['inputs'][str(path.relative_to(ROOT))]=digest(dest)
    def archived(archive,name):
        path=archive/name
        assert digest(path)==json.loads((archive/'manifest.json').read_text())[name]
        freeze(path); return path
    def dag_ok(graph):
        dag=graph['dag']; remaining={c['id'] for c in dag['components']}
        while remaining:
            roots={v for v in remaining if not any(e['target']==v and e['source'] in remaining for e in dag['edges'])}
            if not roots: return False
            remaining-=roots
        return True
    try:
        for p in [Path(__file__).resolve(),ROOT/'scripts/cross_layer_dependencies.py',ROOT/'scripts/jdbc_boundary_contracts.py',
                  ROOT/'scripts/app_db_smoke.py']: freeze(p)
        snap=archived(FACTS,'inputs/prototype/module_pruning/JdbcBoundaryExtract.java')
        # This is the frozen v2 graph regression. The current extractor and its
        # new guard facts are tested separately by test_guarded_dispatch.py.
        check('source AST extraction regression passed',json.loads(archived(FACTS,'run.json').read_text())['checks_passed']==22)
        original=json.loads(archived(FACTS,'original.json').read_text())
        candidate=json.loads(archived(FACTS,'repaired.json').read_text())
        assert original['kind']==candidate['kind']=='jdbc-boundary-slices/v2'
        ddl=archived(E2E,'generated/install.sql').read_text()
        save('protocol.json',dict(scope='two real timestamp write slices, inference of positions by SQL use; no supplied source-to-candidate slot mapping',
          inputs='attributed AST facts for frozen original and repaired helpers; actual E2E SQL',
          roots='all statically identified setTimestamp sites in supplied source files',
          premises=['SQLStmt/Procedure API summary','mutable SQLStmt field initialized as declared, no external replacement','known public schema/search_path','no unmodeled alias mutation'],
          outputs=['typed value/binding/argument/write dependency graph','unresolved dispatch/procedure control nodes','SCC condensation DAG','structural sink correspondences'],
          excluded=['whole-program call graph completeness','path feasibility and exception equivalence','automatic deletion','new DB/timing runs'],
          stop='fixed correspondence and mutation controls, no extra programs or profiling'))
        ref=recover(original); cur=recover(candidate,ddl); mapping=correspond(ref,cur)
        save('original-graph.json',ref); save('candidate-graph.json',cur); save('correspondence.json',mapping)
        check('two original INSERT timestamp destinations recovered',len(ref['paths'])==2 and {p['table']+'.'+p['column'] for p in ref['paths']}=={'oorder.o_entry_d','history.h_date'})
        check('two candidate paths include both Payment dispatch targets',len(cur['paths'])==2 and {p['function'] for p in cur['paths']}=={'reversedb_payment.finish_payment','reversedb_payment.finish_payment_fault'})
        check('configuration-selected NewOrder SQL remains explicitly unknown',not ref['unknowns'] and len(cur['unknowns'])==1 and 'FusedNewOrder#' in cur['unknowns'][0]['binding']['owner'])
        history=next(x for x in mapping if x['sink']=='history.h_date')
        check('Payment slot 6 maps automatically to slot 10 through actual formal use',history['original_slot']==6 and len(history['candidates'])==2 and all(x['jdbc_slot']==10 and x['formal']=='p_time' for x in history['candidates']))
        order=next(x for x in mapping if x['sink']=='oorder.o_entry_d')
        check('NewOrder target is not guessed from the available function body',order['original_slot']==5 and not order['candidates'] and order['status']=='unresolved')
        check('dispatch and procedure controls remain unresolved graph nodes',any(n['kind']=='unresolved_dispatch' for n in cur['nodes']) and any(n['kind']=='unresolved_procedure_control' for n in cur['nodes']))
        check('mutable SQLStmt premise preserved',any('external field mutation unresolved' in p.get('sql_field_premise','') for p in ref['paths']))
        check('actual graphs condense to DAG without losing nodes',dag_ok(ref) and dag_ok(cur) and sum(len(c['members']) for c in cur['dag']['components'])==len(cur['nodes']))
        renamed=copy.deepcopy(candidate)
        for s in renamed['statements']:
            s['sql_alternatives']=[q.replace('reversedb_payment.finish_payment','opaque_fns.write_event') for q in s['sql_alternatives']]
        renamed_sql=ddl.replace('reversedb_payment.finish_payment','opaque_fns.write_event').replace('p_time','event_clock')
        renamed_graph=recover(renamed,renamed_sql); save('renamed-graph.json',renamed_graph)
        check('function and formal renaming preserve recovered column relationships',len(renamed_graph['paths'])==2 and len(renamed_graph['unknowns'])==1 and all(p['formal']=='event_clock' for p in renamed_graph['paths']))
        # Reorder both explicit INSERT columns and values, without changing data flow.
        changed=ddl.replace('H_DATE, H_AMOUNT','H_AMOUNT, H_DATE').replace('p_w,p_time,p_amount,p_history','p_w,p_amount,p_time,p_history')
        reordered=recover(candidate,changed)
        check('INSERT column/value permutation preserves lineage',[(p['table'],p['column']) for p in reordered['paths']]==[(p['table'],p['column']) for p in cur['paths']])
        wrong=recover(candidate,ddl.replace('H_DATE, H_AMOUNT','H_AMOUNT, H_DATE'))
        save('wrong-column-graph.json',wrong)
        check('column-only permutation changes destination rather than inheriting old mapping',not next(x for x in correspond(ref,wrong) if x['sink']=='history.h_date')['candidates'])
        shifted=copy.deepcopy(candidate)
        for b in shifted['bindings']:
            if b['operation']=='setTimestamp' and 'SegmentedPayment#' in b['owner']: b['arguments'][0]['value']=11
        shifted_graph=recover(shifted,ddl)
        check('wrong JDBC slot cannot keep old history-time correspondence',not next(x for x in correspond(ref,shifted_graph) if x['sink']=='history.h_date')['candidates'])
        absent=recover(candidate,'')
        check('missing function bodies remain explicit unknown dependencies',not absent['paths'] and bool(absent['unknowns']))
        overloaded=recover(candidate,ddl+'\n'+ddl)
        check('overloaded/duplicate function definitions remain unknown',not overloaded['paths'] and bool(overloaded['unknowns']))
        overwritten=recover(candidate,ddl.replace('BEGIN\nIF p_bad THEN','BEGIN\np_time := NULL;\nIF p_bad THEN'))
        check('formal reassignment blocks unsupported lineage',not next(x for x in correspond(ref,overwritten) if x['sink']=='history.h_date')['candidates'] and bool(overwritten['unknowns']))
        dynamic=recover(candidate,ddl.replace('BEGIN\nIF p_bad THEN',"BEGIN\nEXECUTE 'SELECT 1';\nIF p_bad THEN"))
        check('dynamic SQL effect is unknown',not next(x for x in correspond(ref,dynamic) if x['sink']=='history.h_date')['candidates'])
        mystery=copy.deepcopy(candidate)
        for s in mystery['statements']: s['sql_alternatives']=[]; s['status']='unknown'
        unknown=recover(mystery,ddl)
        check('unresolved Java SQL target has no invented edges',not unknown['paths'] and bool(unknown['unknowns']))
        fake=functions("CREATE FUNCTION demo.f(t timestamp) RETURNS void LANGUAGE plpgsql AS $x$ BEGIN RAISE NOTICE 'INSERT INTO ghost (date) VALUES (t);'; END; $x$;")
        check('SQL text inside strings does not create writes',fake['demo.f'][0].get('inserts')==[])
        decoy="CREATE FUNCTION ghost.f(t timestamp) RETURNS void LANGUAGE plpgsql AS $x$ BEGIN INSERT INTO ghost (date) VALUES (t); END; $x$;"
        check('commented function declarations are not callable targets',functions('/* '+decoy+' */\n'+ddl).keys()==functions(ddl).keys())
        check('function-looking SQL string is not a declaration',not functions("SELECT '"+decoy+"';"))
        cyclic=dict(nodes=[dict(id=x) for x in ('a','b','c')],edges=[{'from':a,'to':b,'kind':'call'} for a,b in [('a','b'),('b','a'),('b','c')]])
        cyclic['dag']=condense(cyclic['nodes'],cyclic['edges']); save('cycle-condensation.json',cyclic)
        check('recursive cycle is retained inside an SCC, not deleted',dag_ok(cyclic) and any(c['members']==['a','b'] and c['cyclic'] for c in cyclic['dag']['components']) and len(cyclic['edges'])==3)
        self_loop=dict(nodes=[dict(id='self')],edges=[{'from':'self','to':'self','kind':'call'}]); self_loop['dag']=condense(**self_loop)
        check('self recursion stays marked cyclic',self_loop['dag']['components'][0]['cyclic'] and dag_ok(self_loop))
        check('recovered graphs never authorize deletion',not ref['automatic_deletion_authorized'] and not cur['automatic_deletion_authorized'])
        report.update(status='passed',checks_passed=len(report['checks']),original_paths=len(ref['paths']),candidate_paths=len(cur['paths']),
          candidate_nodes=len(cur['nodes']),candidate_edges=len(cur['edges']),new_database_runs=0,new_timing_samples=0,
          conclusion='Automatic timestamp-slice correspondence from attributed SQL factory/binding and actual INSERT use; control and external mutation premises retained, no full program proof')
    except BaseException as error:
        report.update(status='failed',error=repr(error)); raise
    finally:
        save('run.json',report); save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],checks=len(report['checks']),output=str(out))))


if __name__=='__main__': main()
