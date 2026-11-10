#!/usr/bin/env python3
"""Characterize original eager/lazy group effects and reuse the original DP."""
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
from alfio_service_group_contracts import service_group_contract, attach_service_group, OWNER
from grouped_lookup_contracts import grouped_lookup_contract, key_cardinality
from forwarding_contracts import forwarding_values
from placement_frontend import select_complete_contract
from fusion_placement import cost_value, check_contract

FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
DEPS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'
PREVIOUS=ROOT/'evidence/alfio-forwarding/20261106-f5e1b6f4d3f7'


def main():
    out=ROOT/'evidence/alfio-service-groups'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
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
        java=[ROOT/'prototype/alfio'/name for name in ['ForwardingBytecodeExtract.java','AlfioFieldContractProbe.java','AlfioServiceGroupProbe.java']]
        source_root=ROOT/'work/alfio/source-d44407944e58/src/main/java'
        files=java+[Path(__file__).resolve()]+[ROOT/'scripts'/name for name in [
            'alfio_baseline.py','grouped_lookup_contracts.py','alfio_service_group_contracts.py','forwarding_contracts.py',
            'placement_frontend.py','fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        files += [source_root/name for name in [OWNER+'.java','alfio/model/AdditionalServiceItem.java',
            'alfio/model/AdditionalService.java','alfio/model/Event.java',
            'alfio/repository/AdditionalServiceItemRepository.java','alfio/repository/AdditionalServiceRepository.java']]
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
        check('official runtime and ordered classpath verified',True)
        cp=os.pathsep.join(map(str,[original,*libs]));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,*java],'compile.log')
        full_cp=str(classes)+os.pathsep+cp
        run([JDK/'java','-Xmx256m','-XX:ActiveProcessorCount=2','-cp',full_cp,'research.alfio.ForwardingBytecodeExtract',
            out/'bytecode.json',original/(OWNER+'.class'),original/'alfio/model/AdditionalServiceItem.class',
            original/'alfio/repository/AuditingRepository.class'],'extract.log')
        run([JDK/'java','-Xmx256m','-XX:ActiveProcessorCount=2','-cp',full_cp,'research.alfio.AlfioServiceGroupProbe',out],'probe.log')
        probe=json.loads((out/'binary-probe.json').read_text());origin=urlparse(probe['origin'])
        check('official calculator binary executed',origin.scheme=='file' and not origin.netloc and Path(unquote(origin.path)).resolve()==original.resolve())
        check('binary checks passed',probe['status']=='passed')
        bytecode=json.loads((out/'bytecode.json').read_text());facts=json.loads(retain(DEPS,'bytecode-facts.json').read_text())
        source=(source_root/(OWNER+'.java')).read_text()
        child=service_group_contract(facts,bytecode,source);save('service-group-contract.json',child)
        check_contract(child);result=select_complete_contract(child);save('service-group-result.json',result)
        selection=result['selection']
        check('type-based group bound independent of fixtures',child['parameters']=={'groups':[0,2**32]} and not child['scope']['domain_proof']['tight'])
        check('same DP accepts 1 plus G without backend',selection['original_calls']==dict(base=1,terms={'groups':1}) and selection['selected_calls']==selection['original_calls'] and not selection['database_regions'])
        check('worst-case objective belongs to conservative type box',selection['objective']==[1+2**32,0,0])
        check('whole items and grouping control retained',len(child['edges'])==3 and child['nodes'][1]['uses']=={'items':'complete_original_item_list'} and child['nodes'][2]['uses']['groups']=='original_group_map_of_complete_lists')
        check('no invented codecs or lowering',child['wire_types']=={} and all(n['database'] is None for n in child['nodes']))
        check('both original SQL statements retained',len([q for n in child['nodes'] for q in n['queries']])==2)
        for c in probe['cases']:
            if isinstance(c['detail'],dict) and 'groups' in c['detail']:
                check(c['case']+' measured calls agree with symbolic formula',len(c['calls'])==cost_value(selection['original_calls'],{'groups':c['detail']['groups']}))
        flows={}
        for path in sorted(PREVIOUS.glob('*-contract.json')):
            flows[path.name.removesuffix('-contract.json')]=json.loads(retain(PREVIOUS,path.name).read_text())
        enriched,link=attach_service_group(flows,child)
        save('hierarchy.json',json.loads(retain(PREVIOUS,'hierarchy.json').read_text())+[link])
        for name,c in flows.items():
            previous=json.loads(retain(PREVIOUS,name+'-result.json').read_text())
            check(name+' previous selection unchanged',select_complete_contract(enriched[name])==previous)
            if name!='reservation_entry':check(name+' previous contract unchanged',enriched[name]==c)
        root=enriched['reservation_entry'];save('reservation-entry-contract.json',root);save('reservation-entry-result.json',select_complete_contract(root))
        total=next(n for n in root['nodes'] if n['id']=='reservation_total')
        check('subcall cost never substitutes for whole calculator',total['java_calls'] is None and total['source']['known_subcalls'][0]['parent_cost_still_unknown'])
        for changed in [source+'\n']:
            try:service_group_contract(facts,bytecode,changed)
            except ValueError:check('changed source rejected',True)
            else:raise AssertionError('changed source accepted')
        mutated=copy.deepcopy(bytecode);mutated['classes'][0]['sha256']='changed'
        try:service_group_contract(facts,mutated,source)
        except ValueError:check('changed calculator binary rejected',True)
        else:raise AssertionError('changed binary accepted')
        # Unknown reference-key cardinality stays unknown; no sample-size bound.
        unknown=grouped_lookup_contract('Ljava/lang/String;','same read','same lookup',{})
        check('unbounded key domain blocks selection',select_complete_contract(unknown)['selection'] is None)
        check('primitive cardinalities come from type widths',key_cardinality('J')==2**64 and key_cardinality('B')==256 and key_cardinality('Z')==2)
        renamed=grouped_lookup_contract('I','different read','different lookup',{'method':'unrelated/System#collect'})
        renamed_result=select_complete_contract(renamed)['selection']
        check('renamed source preserves shared cost and placement',renamed_result['original_calls']==selection['original_calls'] and renamed_result['objective']==selection['objective'])
        old_bytecode=json.loads(retain(PREVIOUS,'forwarding-bytecode.json').read_text())
        old_audit=next(c for c in old_bytecode['classes'] if c['class']=='alfio/repository/AuditingRepository')
        new_audit=next(c for c in bytecode['classes'] if c['class']==old_audit['class'])
        check('lambda evidence extension preserves every earlier audit forwarding analysis',
              [forwarding_values(m) for m in old_audit['methods']]==[forwarding_values(m) for m in new_audit['methods']])
        save('conclusion.json',dict(status='grouped_lookup_cost_and_value_contract_closed_under_conservative_type_domain',
            binary_cases=len(probe['cases']),binary_checks=len(probe['checks']),scopes=len(enriched),
            phase_nodes=sum(len(c['nodes']) for c in enriched.values()),
            unknown_phase_costs=sum(n['java_calls'] is None for c in enriched.values() for n in c['nodes']),
            source_cost='1 + G, G is mathematical number of distinct original int service IDs',
            theoretical_single_region_call_reduction='G only if a future lowering and complete effects/ABI are independently admitted; not a selected plan',
            limits=['source grouping semantics manually bound; lambda/call facts automatically extracted',
                'primitive type domain is a conservative overapproximation, not an exact reachable workload domain',
                'full cost applies only to normal eager collection; lazy partial consumption and failures keep their own prefixes',
                'proxy calls characterize Java behavior, not actual SQL, transactions or performance',
                'parent calculator remains unknown; no global plan or executable fusion']))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))))


if __name__=='__main__':main()
