#!/usr/bin/env python3
"""Typed caller-use slices joined to prior SQL consumption facts, fixed probes."""
import collections
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from app_db_smoke import ROOT, JDK, LIB, ARCHIVE, digest


def routes(graph,start,ends):
    adjacency=collections.defaultdict(list)
    for e in graph['edges']:adjacency[e['from']].append(e['to'])
    todo=[(start,[start])];seen=set();result=[]
    while todo:
        node,path=todo.pop()
        if node in seen:continue
        seen.add(node)
        if node in ends:result.append(path)
        for target in adjacency[node]:todo.append((target,path+[target]))
    return result


def main():
    out=ROOT/'evidence/caller-observation'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],checks=[],new_database_executions=0,new_model_fits=0)
    def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def freeze(p):
        dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dest)
    def retain(archive,name):
        path=archive/name;assert digest(path)==json.loads((archive/'manifest.json').read_text())[name]
        report['retained_assets'][str(path)]=digest(path);return path
    def check(name,condition):assert condition,name;report['checks'].append(name)
    def run(cmd,log):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120);(out/log).write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Command failed: '+str(out/log))
    try:
        analyzer=ROOT/'prototype/module_pruning/CallerValueExtract.java';probe=ROOT/'prototype/module_pruning/CallerObservationProbe.java'
        dependency=ROOT/'prototype/module_pruning/DependencyExtract.java'
        for p in [Path(__file__).resolve(),analyzer,probe,dependency,ROOT/'scripts/app_db_smoke.py']:freeze(p)
        obs=ROOT/'evidence/tpcc-observation/20261103-8729d2283fdc'
        adapter=retain(obs,'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java')
        runtime_source=retain(obs,'run/inputs/app/tpcc/src/reversedb/tpcc/AppRuntime.java')
        bridge=retain(ROOT/'evidence/module-initialization/20261105-9b9711eca669','bridge-contract.json')
        consumption=retain(ROOT/'evidence/result-consumption/20261106-1857da9685fb','OrderStatus-links.json')
        for p in [adapter,runtime_source,bridge,consumption]:freeze(p)
        bundle=ARCHIVE/'bundle';reference=retain(ARCHIVE,'bundle/reference.jar');libs=sorted(LIB.glob('*.jar'));old=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for p in libs:assert digest(p)==old[str(p)];report['retained_assets'][str(p)]=digest(p)
        for p in sorted((bundle/'candidate-classes').rglob('*.class')):retain(ARCHIVE,str(p.relative_to(ARCHIVE)))
        sources=[retain(ARCHIVE,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+n+'.java') for n in ['NewOrder','Payment','Delivery','OrderStatus','StockLevel']]
        cp=':'.join(map(str,[reference,bundle/'candidate-classes',*libs]));jsonjar=LIB/'json-20240303.jar'
        classes=out/'extractor';classes.mkdir();actual=out/'actual-classes';actual.mkdir();empty=out/'empty';empty.mkdir()
        save('protocol.json',dict(scope='local caller value-use routes to unchanged business JSONObject return; not whole-program safety',
             sources='frozen adapter, existing hash-bound reflection contract and typed target mapping, previous SQL-to-getter facts',
             summaries=['AppRuntime.rows arguments may select returned rows','JSONArray.getJSONObject may select a component','fresh fluent JSONObject.put chain may contribute receiver/argument values'],
             boundary='TpccApp.reference business result; AppRuntime status/transaction wrapper is outside this probe',
             unknowns='reassigned locals, mutated local containers, field writes, escaped objects, opaque calls, repeated JSON keys; return-field preservation remains conditional',
             controls='direct/renamed/alias/field/overwrite/escape/mutable JSON/duplicate key/discarded values',
             runtime='six actual frozen adapter invocations with supplied JDBC responses: baseline, helper ID/carrier/time, discarded display values, discarded display query failure',
             stop='one fixed static/probe set; no score fitting, DB execution, data pruning, or automatic deletion'))
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',classes,analyzer,dependency],'compile.log')
        run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.DependencyExtract',out/'calls.json',cp,bridge,adapter,*sources],'calls.log')
        run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.CallerValueExtract',out/'caller.json',cp,out/'calls.json',adapter],'caller.log')
        g=json.loads((out/'caller.json').read_text());nodes={n['id']:n for n in g['nodes']}
        returns={n['id'] for n in g['nodes'] if n['kind']=='method_return' and '#reference(' in n['owner']}
        roots=[n for n in g['nodes'] if n['kind']=='call_result' and n.get('reflection',{}).get('status')=='verified_bridge_target' and '#reference(' in n['owner']]
        witnesses=[]
        for root in roots:
            target=root['reflection']['target'];paths=routes(g,root['id'],returns)
            witnesses.append(dict(call=root,target=target,value_routes=paths,execution_effects_preserved=True,
                                  value_status='conditional_route' if paths else 'not_connected_in_supported_slice'))
        detail=next(w for w in witnesses if '#getOrderDetails(' in w['target']);display=next(w for w in witnesses if '#getOrderLines(' in w['target'])
        check('verified detail return reaches business JSON',bool(detail['value_routes']))
        check('discarded display value is not a business JSON value edge',not display['value_routes'] and any(n['kind']=='discarded_call_value' and n['call']==display['call']['id'] for n in g['nodes']))
        field_nodes=[n for n in g['nodes'] if n['kind']=='field_read' and routes(g,detail['call']['id'],{n['id']})]
        check('only order ID field used from returned order object',{n['field'] for n in field_nodes}=={'com.oltpbenchmark.benchmarks.tpcc.pojo.Oorder#o_id'})
        joined=[]
        for source in json.loads(consumption.read_text()):
            if '#getOrderDetails(' not in source['owner']:continue
            field=source['getter']['use'].get('target');destinations=[n for n in field_nodes if n['field']==field]
            joined.append(dict(source=source,caller_field_nodes=[n['id'] for n in destinations],
                 response_routes=[p for n in destinations for p in routes(g,n['id'],returns)],
                 status='conditional_sql_to_business_response_route' if destinations else 'no_value_route_in_supported_caller',
                 source_field_preservation_proved=False,call_effects_preserved=True,deletion_authorized=False))
        check('only SQL order-ID getter joins response selection',len(joined)==3 and sum(bool(x['response_routes']) for x in joined)==1)
        save('response-routes.json',dict(call_results=witnesses,joined_order_fields=joined,nodes=g['nodes'],edges=g['edges'],automatic_deletion_authorized=False))
        fixture=out/'CallerExamples.java'
        fixture.write_text('''package research; import org.json.*; public class CallerExamples {
 static class Box { int id; }
 static int seed(){return 1;} static Box box(){return new Box();} static void mutate(Box b){b.id=0;}
 static JSONObject direct(){return new JSONObject().put("x",seed());}
 static JSONObject renamed(){int renamed=seed();return new JSONObject().put("x",renamed);}
 static JSONObject alias(){int a=seed();int b=a;return new JSONObject().put("x",b);}
 static JSONObject reassigned(){int a=seed();a=0;return new JSONObject().put("x",a);}
 static JSONObject field(){Box a=box();return new JSONObject().put("x",a.id);}
 static JSONObject overwritten(){Box a=box();a.id=0;return new JSONObject().put("x",a.id);}
 static JSONObject escaped(){Box a=box();mutate(a);return new JSONObject().put("x",a.id);}
 static JSONObject mutableJson(){JSONObject j=new JSONObject();j.put("x",seed());return j;}
 static JSONObject duplicateKey(){return new JSONObject().put("x",seed()).put("x",0);}
 static JSONObject discarded(){seed();return new JSONObject().put("x",0);}
}''')
        run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.CallerValueExtract',out/'controls.json',cp,out/'calls.json',fixture],'controls.log')
        c=json.loads((out/'controls.json').read_text())
        for name,expected in dict(direct=True,renamed=True,alias=True,reassigned=False,field=True,overwritten=False,escaped=False,mutableJson=False,duplicateKey=False,discarded=False).items():
            roots_=[n for n in c['nodes'] if n['kind']=='call_result' and '#'+name+'(' in n['owner'] and (n['target'].endswith('#seed()') or n['target'].endswith('#box()'))]
            ends={n['id'] for n in c['nodes'] if n['kind']=='method_return' and '#'+name+'(' in n['owner']}
            check('control '+name,len(roots_)==1 and bool(routes(c,roots_[0]['id'],ends))==expected)
        check('no proof flag introduced',not g['automatic_deletion_authorized'])
        save('before-probe-freeze.json',dict(inputs=report['inputs'],checks=report['checks'],response_routes_sha256=digest(out/'response-routes.json')))
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',actual,adapter,sources[3],probe],'compile-actual.log')
        run([JDK/'java','-Duser.timezone=UTC','-cp',str(actual)+':'+cp,'research.CallerObservationProbe',out/'probe.json'],'probe.log')
        result=json.loads((out/'probe.json').read_text());cases={r['variant']:r for r in result['runs']};base=cases['base']['response']
        check('six frozen adapter invocations',len(cases)==6)
        check('changed helper ID changes complete business response',cases['id_changed']['response']!=base and cases['id_changed']['response']['order']['o_id']==202)
        for name in ['carrier_changed','time_changed','display_values_changed']:
            check(name+' leaves complete business response unchanged',cases[name]['status']=='returned' and cases[name]['response']==base)
        check('discarded display call failure still propagates',cases['display_failure']['status']=='error' and cases['display_failure']['sql_state']=='58000')
        for name,case in cases.items():
            queries=[e for e in case['events'] if e['op']=='executeQuery']
            check(name+' really executes discarded display query',sum(e['sql'].startswith('select ol_i_id,') for e in queries)==1)
        check('actual adapter and decompiled helper loaded',str(actual) in result['adapter_origin'] and str(actual) in result['helper_origin'])
        for path,h in report['inputs'].items():assert digest(ROOT/path)==h
        for path,h in report['retained_assets'].items():assert digest(Path(path))==h
        report.update(status='completed',checks_passed=len(report['checks']),adapter_invocations=6,
             conditional_joined_fields=1,nonjoined_fields=2,model_changed=False,whole_application_proved=False,
             conclusion='Order ID has a conditional value route through fresh queries to business JSON; discarded display still affects failure. No operation removal inferred.')
    except BaseException as error:report.update(status='failed',error=repr(error));raise
    finally:
        save('report.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
