#!/usr/bin/env python3
"""Verify a reusable bytecode forwarding frontend and one audit integration."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile
from urllib.parse import unquote, urlparse
from alfio_baseline import ROOT, WORK, JDK, JAR, SHA, digest
from alfio_flow_contracts import build_flow_contracts
from alfio_field_contracts import field_summary, attach_field_summary
from alfio_forwarding_contracts import attach_audit_forwarding
from forwarding_contracts import forwarding_values, close_forwarding, forwarding_dag
from placement_frontend import select_complete_contract
from fusion_placement import check_contract

FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
DEPS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'
BASE=ROOT/'evidence/alfio-baseline/20261106-788b831dd41c'


def main():
    out=ROOT/'evidence/alfio-forwarding'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],commands=[],inputs={},retained_assets={},database_runs=0,timing_samples=0)
    def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def retain(folder,name):
        p=folder/name
        if digest(p)!=json.loads((folder/'manifest.json').read_text())[name]:raise AssertionError(p)
        report['retained_assets'][str(p)]=digest(p);return p
    def run(command,log):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (out/log).write_bytes(p.stdout)
        if p.returncode:raise RuntimeError(str(out/log))
    try:
        java=[ROOT/'prototype/alfio'/name for name in ['ForwardingBytecodeExtract.java','ForwardingFixture.java','AlfioForwardingProbe.java']]
        files=java+[Path(__file__).resolve()]+[ROOT/'scripts'/name for name in [
            'alfio_baseline.py','forwarding_contracts.py','alfio_forwarding_contracts.py','alfio_field_contracts.py',
            'alfio_flow_contracts.py','alfio_placement_contracts.py','placement_frontend.py',
            'fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        source_root=ROOT/'work/alfio/source-d44407944e58/src/main/java'
        files += [source_root/'alfio/repository'/name for name in ['PurchaseContextFieldRepository.java','AuditingRepository.java']]
        for p in files:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
            report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged optimizer '+name,digest(ROOT/'scripts'/name)==digest(retain(FIRST,'inputs/scripts/'+name)))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text());old=json.loads(retain(FIRST,name+'-selection.json').read_text())
            check(name+' exact original selection',select_complete_contract(c)['selection']==old)
        check('official jar digest',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        binary=WORK/'official-binary';original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    if digest(binary/member)!=hashlib.sha256(jar.read(member)).hexdigest():raise AssertionError(member)
        check('official complete runtime and classpath verified',True)
        cp=os.pathsep.join(map(str,[original,*libs]));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,*java],'compile.log')
        full_cp=str(classes)+os.pathsep+cp
        run([JDK/'java','-Xmx256m','-XX:ActiveProcessorCount=2','-cp',full_cp,'research.alfio.ForwardingBytecodeExtract',
             out/'forwarding-bytecode.json',original/'alfio/repository/AuditingRepository.class',classes/'research/alfio/ForwardingFixture.class'],'extract.log')
        run([JDK/'java','-Xmx256m','-XX:ActiveProcessorCount=2','-cp',full_cp,'research.alfio.AlfioForwardingProbe',out],'probe.log')
        probe=json.loads((out/'binary-probe.json').read_text());origin=urlparse(probe['origin'])
        check('official audit binary executed',origin.scheme=='file' and not origin.netloc and Path(unquote(origin.path)).resolve()==original.resolve())
        check('binary behavior checks passed',probe['status']=='passed')
        bytecode=json.loads((out/'forwarding-bytecode.json').read_text())
        facts=json.loads(retain(DEPS,'bytecode-facts.json').read_text())
        repository=retain(BASE,'decompiled/alfio/repository/TicketRepository.java').read_text()
        manager=retain(BASE,'decompiled/alfio/manager/TicketReservationManager.java').read_text()
        flows,links=build_flow_contracts(facts,repository,manager)
        flows=attach_field_summary(flows,field_summary(facts,(source_root/'alfio/repository/PurchaseContextFieldRepository.java').read_text()))
        enriched,link,summary=attach_audit_forwarding(flows,facts,bytecode);links.append(link)
        save('audit-summary.json',summary);save('hierarchy.json',links)
        check('eight SQL parameters recovered in exact order',[b['sql_parameter'] for b in summary['sql_bindings']]==[
            'reservationId','userId','eventId','eventType','eventTime','entityType','entityId','modifications'])
        args=summary['events'][0]['arguments']
        check('original seven references and appended null recovered',
              [a.get('position') for a in args[:7]]==list(range(7)) and args[7]==dict(kind='constant',type='null',value=None))
        check('equal values never merge parameter identities',args[0]!=args[6])
        check('return comes from exact SQL call',summary['return_value']==dict(kind='terminal_result',event=0,type='I'))
        check('original null cast retained as provenance',len(summary['direct_null_casts'])==1 and summary['direct_null_casts'][0]['target']=='java/lang/String')
        for name in flows:
            if name!='reservation_entry':check(name+' unchanged',enriched[name]==flows[name])
        root=enriched['reservation_entry'];old=flows['reservation_entry']
        check('root effects and frames retained',root['edges']==old['edges'] and all(n['database'] is None for n in root['nodes']) and
              all(n['uses']==o['uses'] and n['defines']==o['defines'] for n,o in zip(root['nodes'],old['nodes'])))
        check('only audit phase cost refined',all(n==o for n,o in zip(root['nodes'],old['nodes']) if n['id']!='audit_creation'))
        results={}
        for name,c in enriched.items():
            save(name+'-contract.json',c);results[name]=select_complete_contract(c);save(name+'-result.json',results[name])
        child=enriched['audit_forwarding'];check_contract(child)
        check('same DP consumes exact forwarding DAG',results['audit_forwarding']['selection']['original_calls']==dict(base=1,terms={}) and not results['audit_forwarding']['selection']['database_regions'])
        check('concrete return dependency recovered',any(e['kind']=='value' and e['resource']=='result_0' for e in child['edges']))
        check('global remains incomplete',results['reservation_entry']['selection'] is None and len(results['reservation_entry']['unresolved_costs'])==8)
        check('field domain uncertainty preserved',results['ordinary_initialization']['selection'] is None and len(results['ordinary_initialization']['unresolved_parameters'])==2)
        mutated=copy.deepcopy(bytecode);mutated['classes'][0]['sha256']='bad'
        try:attach_audit_forwarding(flows,facts,mutated)
        except ValueError:check('changed binary cannot reuse audit contract',True)
        else:raise AssertionError('changed binary accepted')
        fixture=next(c for c in bytecode['classes'] if c['class']=='research/alfio/ForwardingFixture')
        methods={m['id']:m for m in fixture['methods']}
        def named(name):return next(m for m in methods.values() if m['id'].split('#')[1].split('(')[0]==name)
        terminal={named('sink')['id']:dict(java_calls=dict(base=1,terms={}),return_type='I',authority='synthetic test effect')}
        controls={}
        for name in ['forward','delegate','discarded','twice','branch','catches','external','cast','recursive']:
            m=named(name);controls[name]=close_forwarding(m['id'],methods,terminal)
        for name in ['forward','delegate']:
            r=controls[name];a=r['events'][0]['arguments']
            check(name+' same-reference alias and wide-slot mapping',r['status']=='closed_forwarding' and a[0]==a[1] and a[2]==dict(kind='parameter',position=1,type='J'))
        check('discarded result retains effect',len(controls['discarded']['events'])==1 and controls['discarded']['return_value']==dict(kind='constant',type='I',value=7))
        check('two effects counted and ordered',controls['twice']['java_calls']==dict(base=2,terms={}) and controls['twice']['return_value']['event']==1)
        for name in ['branch','catches','external','cast','recursive']:
            check(name+' remains unknown without partial cost',controls[name]['status']=='unknown' and 'java_calls' not in controls[name])
        for field,value in [('owner_access',1),('access',9)]:
            unsupported=copy.deepcopy(named('forward'));unsupported[field]=value
            check(field+' cannot bypass interface-default scope',forwarding_values(unsupported)['status']=='unknown')
        invalid=copy.deepcopy(terminal);invalid[named('sink')['id']]['return_type']='J'
        check('terminal result type mismatch rejected',close_forwarding(named('forward')['id'],methods,invalid)['status']=='unknown')
        for bad in [dict(base=1,terms={'n':1}),dict(base=-1,terms={}),dict(base=True,terms={})]:
            invalid=copy.deepcopy(terminal);invalid[named('sink')['id']]['java_calls']=bad
            check('invalid terminal cost '+repr(bad),close_forwarding(named('forward')['id'],methods,invalid)['status']=='unknown')
        for name in ['forward','delegate','discarded','twice']:
            dag=forwarding_dag(named(name),controls[name],'synthetic_transaction');check_contract(dag)
            check(name+' DAG preserves all terminal calls',len(dag['nodes'])==len(controls[name]['events'])+1)
        # Rename every method and owner: no package/business-name selection logic.
        renamed=copy.deepcopy(methods);mapping={key:key.replace('research/alfio/ForwardingFixture','different/System').replace('#forward(', '#renamed(') for key in methods}
        for m in renamed.values():
            m['id']=mapping[m['id']];m['owner']='different/System'
            for ins in m['instructions']:
                if ins.get('target') in mapping:ins['target']=mapping[ins['target']];ins['owner']='different/System'
        renamed={m['id']:m for m in renamed.values()}
        renamed_result=close_forwarding(mapping[named('delegate')['id']],renamed,{mapping[named('sink')['id']]:next(iter(terminal.values()))})
        check('method and package renaming preserves summary',renamed_result['status']=='closed_forwarding' and renamed_result['java_calls']==controls['delegate']['java_calls'] and renamed_result['events'][0]['arguments']==controls['delegate']['events'][0]['arguments'])
        save('synthetic-controls.json',controls)
        save('audit-overload-coverage.json',[forwarding_values(m) for m in bytecode['classes'][0]['methods']])
        save('conclusion.json',dict(status='forwarding_provenance_recovered_same_optimizer_retained',
            binary_checks=len(probe['checks']),scopes=len(enriched),phase_nodes=sum(len(c['nodes']) for c in enriched.values()),
            unknown_phase_costs=sum(n['java_calls'] is None for c in enriched.values() for n in c['nodes']),
            global_plan=None,limits=['straight-line supported bytecodes only; branches, handlers and external dispatch remain unknown',
                'explicit source-bound terminal and original default-dispatch assumptions; no universal virtual-call proof',
                'binary proxy tests do not execute SQL or prove transaction behavior',
                'only wrapper parameter/call-result dependencies recovered; parent frames remain conservative',
                'no database lowering, serialization, capability deletion or new timing samples']))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))))


if __name__=='__main__':main()
