#!/usr/bin/env python3
"""Bounded typed cursor extraction and real-helper JDBC-row probes."""
import collections
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from app_db_smoke import ROOT, JDK, LIB, ARCHIVE, digest
from value_effect_ir import parse_sql


def link(facts, consumers):
    statements={s['receiver']:s for s in facts['statements']};links=[]
    for cursor in consumers['result_sets']:
        producer=cursor['producer'];spec=statements.get(producer['statement_receiver']) if producer else None
        for call in cursor['calls']:
            if 'use' not in call:continue
            alternatives=[]
            for sql in spec['sql_alternatives'] if spec else []:
                parsed=parse_sql(sql);matches=[]
                if parsed['status']=='represented' and parsed['ir']['kind']=='select':
                    for projection in parsed['ir']['projections']:
                        column=call.get('column');value=projection['value']
                        label=projection['alias'] or (value['name'].split('.')[-1] if value['op']=='column_reference' else None)
                        if type(column) is int and column==projection['position'] or isinstance(column,str) and column.lower()==label:
                            matches.append(projection)
                alternatives.append(dict(sql=sql,parse_status=parsed['status'],projection_matches=matches))
            links.append(dict(owner=cursor['owner'],cursor_id=cursor['id'],cursor_shape=cursor['cursor_shape'],
                              getter=call,alternatives=alternatives,
                              status='linked_projection' if alternatives and all(len(x['projection_matches'])==1 for x in alternatives) else 'unknown_projection',
                              application_observation_connected=False,value_preservation_proved=False))
    return links


def main():
    out=ROOT/'evidence/result-consumption'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',inputs={},retained_assets={},commands=[],checks=[],new_database_executions=0,new_model_fits=0)
    def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def freeze(p):
        dest=out/'inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        report['inputs'][str(p.relative_to(ROOT))]=digest(dest)
    def retain(archive,name):
        path=archive/name;assert digest(path)==json.loads((archive/'manifest.json').read_text())[name]
        report['retained_assets'][str(path)]=digest(path);return path
    def check(name,value):assert value,name;report['checks'].append(name)
    def run(cmd,log):
        cmd=list(map(str,cmd));report['commands'].append(cmd)
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120);(out/log).write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Command failed: '+str(out/log))
    try:
        analyzer=ROOT/'prototype/module_pruning/ResultConsumptionExtract.java';probe=ROOT/'prototype/module_pruning/ResultConsumptionProbe.java'
        for p in [Path(__file__).resolve(),analyzer,probe,*[ROOT/'scripts'/n for n in ['app_db_smoke.py','value_effect_ir.py','cross_layer_dependencies.py','jdbc_boundary_contracts.py']]]:freeze(p)
        reference=retain(ARCHIVE,'bundle/reference.jar');libs=sorted(LIB.glob('*.jar'));retained=json.loads((ARCHIVE/'retained-assets.json').read_text())
        for p in libs:assert digest(p)==retained[str(p)];report['retained_assets'][str(p)]=digest(p)
        cp=':'.join(map(str,[reference,*libs]));jsonjar=LIB/'json-20240303.jar'
        classes=out/'extractor';classes.mkdir();actual=out/'actual-classes';actual.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','23','-proc:none','-cp',jsonjar,'-d',classes,analyzer],'compile-extractor.log')
        save('protocol.json',dict(scope='cursor consumption and local return candidates; not risk classification or complete application lineage',
             development_example='OrderStatus first-row vs iteration; all five existing modules are now exposed development material, not a new blind holdout',
             static_inputs=['NewOrder','Payment','Delivery','OrderStatus','StockLevel'],
             controls='fixed 13 cursor structures, including loop early exit, aliases, escapes, movement, reassignment and dynamic getter column',
             runtime='four actual decompiled helper methods; supplied row lists of length 0/1/2/5 plus changed tails and reversed first rows; 28 invocations',
             stop='one fixed extraction/probe set; no model refit, target-specific risk rules, database simulation or automatic deletion',
             limits=['single_advance is a syntactic cursor descriptor, not an equivalence proof','iteration_capable does not mean every row is consumed',
                     'field-to-return links remain candidates; aliases, overwrites, finally and caller use are not proved',
                     'JDBC row double does not execute SQL, model constraints, driver effects, full endpoint state or transactions']))
        bodies={
            'first':('if(!r.next()) throw new SQLException(); return r.getInt(1);','single_advance'),
            'renamed':('if(!cursor.next()) throw new SQLException(); return cursor.getInt(1);','single_advance'),
            'iterate':('int n=0; while(r.next()) n+=r.getInt(1); return n;','iteration_capable'),
            'break_loop':('while(r.next()) { return r.getInt(1); } return 0;','iteration_capable'),
            'twice':('r.next(); r.next(); return r.getInt(1);','multiple_advances'),
            'movement':('r.absolute(2); return r.getInt(1);','unknown'),
            'alias':('ResultSet a=r; if(a.next()) return a.getInt(1); return 0;','unknown'),
            'escape':('sink(r); r.next(); return r.getInt(1);','unknown'),
            'capture':('Runnable task=()->{try {r.next();} catch(SQLException e){throw new RuntimeException(e);}}; task.run(); return r.getInt(1);','unknown'),
            'reassigned':('r=s.executeQuery(); r.next(); return r.getInt(1);','unknown'),
            'outer_loop':('r.next(); return r.getInt(1);','single_advance'),
            'dynamic_column':('r.next(); return r.getInt(column);','single_advance'),
            'unread':('return 1;','no_advance')}
        text='package research; import java.sql.*; public class CursorExamples { static void sink(ResultSet r) {}\n'
        for name,(body,_) in bodies.items():
            cursor='cursor' if name=='renamed' else 'r'
            header=f'static int {name}(Connection c,int column) throws Exception {{ '
            start='for(int i=0;i<2;i++) {' if name=='outer_loop' else ''
            # Non-resource cursor permits the intentional reassignment control.
            text+=header+start+'try(PreparedStatement s=c.prepareStatement("SELECT 1")) { ResultSet '+cursor+'=s.executeQuery(); '+body+' } '+('} return 0; ' if name=='outer_loop' else '')+'}\n'
        text+='}\n';fixture=out/'CursorExamples.java';fixture.write_text(text)
        def extract(name,paths):
            run([JDK/'java','-cp',str(classes)+':'+str(jsonjar),'research.ResultConsumptionExtract',out/(name+'.json'),cp,*paths],name+'.log')
            return json.loads((out/(name+'.json')).read_text())
        controls=extract('controls',[fixture])
        for name,(_,expected) in bodies.items():
            hits=[r for r in controls['result_sets'] if '#'+name+'(' in r['owner'] and r['producer'] is not None]
            check(name+' cursor descriptor',len(hits)==1 and hits[0]['cursor_shape']==expected)
        dynamic=next(r for r in controls['result_sets'] if '#dynamic_column(' in r['owner'])
        check('dynamic column does not become guessed ordinal',any(c.get('column_reason')=='dynamic_column' and c['column'] is None for c in dynamic['calls']))
        check('loop early return never certified as all rows',all(not r['all_rows_consumed_proved'] for r in controls['result_sets']))
        sources={};alllinks=[];summary={}
        for name in ['NewOrder','Payment','Delivery','OrderStatus','StockLevel']:
            source=retain(ARCHIVE,'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/'+name+'.java');sources[name]=source
            facts=extract(name,[source]);prior=ROOT/('evidence/value-effect-transfer/20261106-a0c82b14a2b0' if name in ('OrderStatus','StockLevel') else 'evidence/module-transfer/20261106-1b72049103e9')
            binding=json.loads(retain(prior,name+'-facts.json').read_text());linked=link(binding,facts);save(name+'-links.json',linked);alllinks+=linked
            summary[name]=dict(result_sets=len(facts['result_sets']),cursor_shapes=dict(collections.Counter(r['cursor_shape'] for r in facts['result_sets'])),
                 getter_sites=len(linked),linked_projection_sites=sum(x['status']=='linked_projection' for x in linked))
        save('static-summary.json',summary)
        first=next(r for r in json.loads((out/'OrderStatus.json').read_text())['result_sets'] if '#getOrderDetails(' in r['owner'])
        loop=next(r for r in json.loads((out/'OrderStatus.json').read_text())['result_sets'] if '#getOrderLines(' in r['owner'])
        check('real first-row helper identified structurally',first['cursor_shape']=='single_advance')
        check('real line formatter retains iteration',loop['cursor_shape']=='iteration_capable')
        fields=[x for x in alllinks if '#getOrderDetails(' in x['owner']]
        check('three order fields link SQL projection to returned-object candidates',len(fields)==3 and all(x['status']=='linked_projection' and x['getter']['use'].get('same_object_return_lines') and not x['value_preservation_proved'] for x in fields))
        run([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',actual,
             *[sources[n] for n in ['OrderStatus','Delivery','StockLevel']],probe],'compile-actual.log')
        run([JDK/'java','-Duser.timezone=UTC','-cp',str(actual)+':'+cp,'research.ResultConsumptionProbe',out/'probe.json'],'probe.log')
        result=json.loads((out/'probe.json').read_text());runs={(r['method'],r['case']):r for r in result['runs']}
        check('exactly 28 actual helper invocations',len(runs)==28)
        for method in ['order_first','delivery_first','stock_first']:
            for case in ['one','two','five','two_tail_changed','five_tail_changed','two_reversed']:
                r=runs[method,case];check(method+' '+case+' reads only row zero',r['status']=='returned' and r['next_calls']==1 and r['read_row_indices']==[0])
            check(method+' tail edits leave result unchanged',runs[method,'two']['result']==runs[method,'two_tail_changed']['result'] and runs[method,'five']['result']==runs[method,'five_tail_changed']['result'])
            check(method+' reversing first two changes result',runs[method,'two']['result']!=runs[method,'two_reversed']['result'])
        for case in ['empty','one','two','five','two_tail_changed','five_tail_changed','two_reversed']:
            r=runs['order_loop',case];size=len(r['input_rows']);check('real iterator '+case+' reads every supplied row',r['status']=='returned' and r['next_calls']==size+1 and r['read_row_indices']==list(range(size)))
        check('real iterator is sensitive to changed tail',runs['order_loop','two']['result']!=runs['order_loop','two_tail_changed']['result'])
        check('all probes load freshly compiled decompiled classes',all(str(actual) in r['class_origin'] for r in result['runs']))
        for path,sha in report['inputs'].items():assert digest(ROOT/path)==sha
        for path,sha in report['retained_assets'].items():assert digest(Path(path))==sha
        report.update(status='completed',static_summary=summary,actual_helper_invocations=28,checks_passed=len(report['checks']),
                      application_observation_connected=False,model_changed=False,
                      conclusion='Cursor consumption distinction recovered and checked on real helper bodies; SQL-to-getter links and returned-object candidates are not a complete app-observation proof.')
    except BaseException as error:report.update(status='failed',error=repr(error));raise
    finally:
        save('report.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
