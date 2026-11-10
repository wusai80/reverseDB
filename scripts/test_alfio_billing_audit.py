#!/usr/bin/env python3
"""Close the local billing/audit effect order without moving its host clock."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import uuid
import zipfile
from alfio_baseline import ROOT, WORK, JDK, JAR, SHA, digest
from alfio_billing_audit_contracts import billing_audit_contract
from partial_placement import select_partial_contract
from placement_frontend import select_complete_contract

PREVIOUS=ROOT/'evidence/alfio-shared-method/20261106-0a751681f789'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
INDEX=ROOT/'evidence/alfio-structure/20261106-e2dc533105d2'
OWNER='alfio/manager/TicketReservationManager'


def clock_model(order,update_fails=False):
    """Explicit effect-model counterexample, not Java execution or a DB test."""
    now=100;stamp=None;events=[]
    for effect in order:
        events.append(effect)
        if effect=='update':
            if update_fails:return dict(events=events,failure='update',audit_timestamp=None)
            now=200
        elif effect=='host_clock':stamp=now
        elif effect=='audit':return dict(events=events,failure=None,audit_timestamp=stamp)
    raise AssertionError(order)


def main():
    out=ROOT/'evidence/alfio-billing-audit'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],inputs={},retained_assets={},commands=[],database_runs=0,timing_samples=0,deleted_modules=0)
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def retain(folder,name):
        p=folder/name;assert digest(p)==json.loads((folder/'manifest.json').read_text())[name]
        report['retained_assets'][str(p)]=digest(p);return p
    def run(command,log):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (out/log).write_bytes(p.stdout)
        if p.returncode:raise RuntimeError(str(out/log))
        return p.stdout.decode()
    try:
        for name in ['scripts/test_alfio_billing_audit.py','scripts/alfio_billing_audit_contracts.py','scripts/partial_placement.py',
                     'scripts/placement_frontend.py','scripts/alfio_baseline.py','scripts/fusion_placement.py',
                     'scripts/fusion_regions.py','scripts/fusion_placement_contracts.py','prototype/alfio/ForwardingBytecodeExtract.java']:
            p=ROOT/name;dst=out/'inputs'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst);report['inputs'][name]=digest(p)
        check('official jar SHA',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        source_archive=WORK/'source-d44407944e58.tar.gz'
        check('source archive pinned',digest(source_archive)=='2d8f4e607d9b23c341a0cdc6ab2b423ce9fe63b74139badca97b57c14c0c3386')
        report['retained_assets'][str(source_archive)]=digest(source_archive)
        sources={}
        with tarfile.open(source_archive) as archive:
            for name in [OWNER,'alfio/repository/TicketReservationRepository','alfio/repository/AuditingRepository','alfio/model/TotalPrice']:
                suffix='/src/main/java/'+name+'.java';matches=[m for m in archive.getmembers() if m.name.endswith(suffix)]
                assert len(matches)==1
                data=archive.extractfile(matches[0]).read();p=WORK/'source-d44407944e58/src/main/java'/(name+'.java')
                check(name+' original source verified',p.read_bytes()==data)
                dst=out/'sources'/(name+'.java');dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data);sources[name]=data.decode()
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged '+name,digest(ROOT/'scripts'/name)==digest(retain(FIRST,'inputs/scripts/'+name)))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text())
            check(name+' exact original selection',select_complete_contract(c)['selection']==json.loads(retain(FIRST,name+'-selection.json').read_text()))
        parent=json.loads(retain(PREVIOUS,'reservation_entry-contract.json').read_text())
        global_graph=json.loads(retain(INDEX,'business-graph.json').read_text())
        check('parent still in indexed business envelope',parent['scope']['method'] in global_graph['methods_in_structural_envelope'])
        binary=WORK/'official-binary';original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for p in libs:
                assert digest(p)==hashlib.sha256(jar.read('BOOT-INF/lib/'+p.name)).hexdigest()
            classfile=original/(OWNER+'.class')
            check('original manager binary verified',digest(classfile)==hashlib.sha256(jar.read('BOOT-INF/classes/'+OWNER+'.class')).hexdigest()==parent['source_class_sha256'])
        cp=os.pathsep.join(map(str,libs));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,
             ROOT/'prototype/alfio/ForwardingBytecodeExtract.java'],'compile.log')
        run([JDK/'java','-cp',str(classes)+os.pathsep+cp,'research.alfio.ForwardingBytecodeExtract',out/'bytecode.json',classfile],'extract.log')
        bytecode=json.loads((out/'bytecode.json').read_text());methods={m['id']:m for m in bytecode['classes'][0]['methods']}
        java_date=run([JDK/'javap','-c','-p','java.util.Date'],'jdk-date-javap.log')
        begin=java_date.index('public java.util.Date();');end=java_date.index('public java.util.Date(',begin+len('public java.util.Date();'))
        date_constructor=java_date[begin:end];(out/'jdk-date-constructor.txt').write_text(date_constructor)
        for tool in ['java','javac','javap']:report['retained_assets'][str(JDK/tool)]=digest(JDK/tool)
        child,bindings,enriched=billing_audit_contract(parent,methods,date_constructor)
        manager=sources[OWNER]
        check('original arithmetic expression retained',
              '(vatStatus == VatStatus.INCLUDED ? totalPrice.getPriceWithVAT() : totalPrice.getPriceWithVAT() - totalPrice.getVAT())' in manager
              and '+ Math.abs(totalPrice.getDiscount())' in manager)
        check('exact billing expression retained',
              'updateBillingData(event.getVatStatus(), calculateSrcPrice(vatStatus, totalPrice), totalPrice.getPriceWithVAT(), totalPrice.getVAT(), Math.abs(totalPrice.getDiscount()), event.getCurrency(), null, null, false, reservationId)' in manager)
        check('exact audit expression retained',
              'auditingRepository.insert(reservationId, null, event.getId(), Audit.EventType.RESERVATION_CREATE, new Date(), Audit.EntityType.RESERVATION, reservationId)' in manager)
        for n in child['nodes']:
            for sql in n['queries']:
                check('exact SQL retained '+n['id'],any(sql==q for old in parent['nodes'] for q in old['queries']))
        result=select_complete_contract(child);save('contract.json',child);save('bindings.json',bindings);save('selection.json',result)
        check('same selector keeps two original calls',result['selection']['original_calls']==result['selection']['selected_calls']==dict(base=2,terms={}) and not result['selection']['database_regions'])
        check('host preparation explicitly retained',all(n['host_retention']['required'] for n in child['nodes'] if n['java_calls']['base']==0))
        for node_id in ['prepare_billing','prepare_audit_after_update']:
            fake=copy.deepcopy(child);node=next(n for n in fake['nodes'] if n['id']==node_id)
            node['database']=dict(lowering='synthetic-guarantee-claim',verified_source_contract=True,guarantees=node['requires'],transaction_policy='same_outer_transaction')
            check(node_id+' cannot be moved by an asserted guarantee',select_complete_contract(fake)['status']=='retention_conflict_no_plan')
        hypothetical=copy.deepcopy(child)
        for n in hypothetical['nodes']:
            if n['java_calls']['base']==1:
                n['queries']=['select value from example where id = ?']
                n['database']=dict(lowering='synthetic-independent-single-statement',verified_source_contract=True,guarantees=n['requires'],transaction_policy='same_outer_transaction')
        types={t for n in hypothetical['nodes'] for t in [*n['uses'].values(),*n['defines'].values()]}
        hypothetical['wire_types']={t:dict(host_to_database=True,database_to_host=True) for t in types}
        h=select_complete_contract(hypothetical);save('hypothetical-capability-control.json',dict(contract=hypothetical,result=h,authority='synthetic only; SQL substituted solely to isolate clock-barrier behavior'))
        check('even hypothetical SQL capabilities cannot cross host clock',
              h['selection']['selected_calls']==dict(base=2,terms={}) and not h['selection']['database_regions']
              and any(i['start']==1 and i['stop']==4 and any(r.get('node')=='prepare_audit_after_update' for r in i['reasons']) for i in h['selection']['rejected_intervals']))
        original_model=clock_model(['update','host_clock','audit']);early=clock_model(['host_clock','update','audit'])
        failure=clock_model(['update','host_clock','audit'],True)
        check('effect model distinguishes early clock from original order',original_model['audit_timestamp']==200 and early['audit_timestamp']==100)
        check('effect model stops before clock on update failure',failure['events']==['update'])
        save('clock-effect-model.json',dict(authority='deterministic model counterexample; not executed original Java or SQL',original=original_model,early_clock=early,update_failure=failure,
             fixed_clock_limitation='a test forcing all clock reads to one value can mask this successful-path timestamp difference'))
        for name in [p.name.removesuffix('-contract.json') for p in PREVIOUS.glob('*-contract.json')]:
            old=json.loads(retain(PREVIOUS,name+'-contract.json').read_text());c=enriched if name=='reservation_entry' else old
            r=select_partial_contract(c);before=json.loads(retain(PREVIOUS,name+'-application.json').read_text())
            check(name+' whole selection unchanged',r['whole']==before['whole'])
            check(name+' local choices unchanged',[i['result'] for i in r['islands']]==[i['result'] for i in before['islands']])
            if name=='reservation_entry':save('reservation-entry-contract.json',c);save('reservation-entry-application.json',r)
        save('hierarchy.json',json.loads(retain(PREVIOUS,'hierarchy.json').read_text())+[
            dict(parent='reservation_entry',nodes=['billing_java_values','update_billing','audit_creation'],child='billing_audit',
                 kind='same original interval refined; overlapping parent/child never counted twice')])
        save('conclusion.json',dict(status='inter_statement_host_clock_explicitly_retained',normal_calls=2,selected_calls=2,
             backend_admitted=False,source_order=['original billing arguments','UPDATE return','original host Date creation','audit INSERT'],
             numeric_and_data_policy='all original arguments and Java numeric evaluation retained; no data-domain cut',
             scope='nine overlapping semantic scopes after attaching this four-stage child; no automatic whole-method semantics',
             decision='stop pursuing one-shot UPDATE+audit fusion under current clock contract; no host/server clock substitution or earlier time capture'))
        for p,sha in report['retained_assets'].items():assert digest(Path(p))==sha
        report.update(status='passed',new_child_phases=4,normal_calls=2,selected_calls=2,actual_database_regions=0)
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))),flush=True)


if __name__=='__main__':main()
