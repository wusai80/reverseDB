#!/usr/bin/env python3
"""One bounded characterization run: original binary + shared frontend, no DB."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from alfio_baseline import ROOT, WORK, JDK, JAR, SHA, digest
from alfio_flow_contracts import build_flow_contracts
from alfio_field_contracts import field_summary, attach_field_summary
from placement_frontend import select_complete_contract
from fusion_placement import cost_value

FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
DEPS=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'
BASE=ROOT/'evidence/alfio-baseline/20261106-788b831dd41c'


def main():
    out=ROOT/'evidence/alfio-field-contract'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    report=dict(status='running',checks=[],commands=[],inputs={},retained_assets={},database_runs=0,timing_samples=0)
    def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok):
        if not ok:raise AssertionError(name)
        report['checks'].append(name)
    def retain(folder,name):
        p=folder/name;check('frozen input '+name,digest(p)==json.loads((folder/'manifest.json').read_text())[name])
        report['retained_assets'][str(p)]=digest(p);return p
    def run(command,log):
        command=list(map(str,command));report['commands'].append(command)
        p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (out/log).write_bytes(p.stdout)
        if p.returncode:raise RuntimeError(str(out/log))
    try:
        probe=ROOT/'prototype/alfio/AlfioFieldContractProbe.java'
        sources=[probe,Path(__file__).resolve()]+[ROOT/'scripts'/name for name in [
            'alfio_baseline.py','alfio_field_contracts.py','alfio_flow_contracts.py','alfio_placement_contracts.py',
            'placement_frontend.py','fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']]
        source_root=ROOT/'work/alfio/source-d44407944e58/src/main/java'
        sources += [source_root/name for name in ['alfio/repository/PurchaseContextFieldRepository.java',
            'alfio/model/PurchaseContextFieldConfiguration.java','alfio/model/PurchaseContextFieldValue.java',
            'alfio/model/Event.java','alfio/util/EventUtil.java','alfio/model/modification/AttendeeData.java']]
        for p in sources:
            dst=out/'inputs'/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
            report['inputs'][str(p.relative_to(ROOT))]=digest(dst)
        for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check('unchanged optimizer '+name,digest(ROOT/'scripts'/name)==digest(retain(FIRST,'inputs/scripts/'+name)))
        for name in ['neworder','payment']:
            c=json.loads(retain(FIRST,name+'-contract.json').read_text())
            old=json.loads(retain(FIRST,name+'-selection.json').read_text())
            check(name+' exact original selection',select_complete_contract(c)['selection']==old)
        facts=json.loads(retain(DEPS,'bytecode-facts.json').read_text())
        repository=retain(BASE,'decompiled/alfio/repository/TicketRepository.java').read_text()
        manager=retain(BASE,'decompiled/alfio/manager/TicketReservationManager.java').read_text()
        field_source=(source_root/'alfio/repository/PurchaseContextFieldRepository.java').read_text()
        summary=field_summary(facts,field_source);save('field-summary.json',summary)
        try:field_summary(facts,field_source+'\n')
        except ValueError:check('changed source rejected',True)
        else:raise AssertionError('unsupported source accepted')
        flows,links=build_flow_contracts(facts,repository,manager)
        enriched=attach_field_summary(flows,summary)
        for name in flows:
            if name!='ordinary_initialization':check(name+' unchanged',enriched[name]==flows[name])
        c=enriched['ordinary_initialization'];save('ordinary-initialization-contract.json',c)
        with patch('placement_frontend.choose_placement',side_effect=AssertionError('unbounded input reached optimizer')):
            result=select_complete_contract(c)
        save('ordinary-initialization-result.json',result)
        check('formula recovered but domains still block plan',result['selection'] is None and not result['unresolved_costs'] and
              {r['parameter'] for r in result['unresolved_parameters']}=={'additional_attendees','additional_mutations'})
        check('conservative value edges unchanged',c['edges']==flows['ordinary_initialization']['edges'] and c['wire_types']=={})
        check('no lowering authorization',all(n['database'] is None for n in c['nodes']))
        cost=c['nodes'][-1]['java_calls']
        check('general symbolic cost includes empty and nonempty cases',
              [cost_value(cost,dict(additional_attendees=a,additional_mutations=w)) for a,w in [(0,0),(1,0),(1,4),(3,7)]]==[0,2,6,13])
        # Finite domains here are gate unit tests only, NOT legal application bounds.
        for bounds in [None,[-1,3],[3,1],[0,1.5],[False,True],[]]:
            synthetic=copy.deepcopy(c);synthetic['parameters']={k:bounds for k in c['parameters']}
            with patch('placement_frontend.choose_placement',side_effect=AssertionError('invalid domain reached optimizer')):
                check('reject domain '+repr(bounds),select_complete_contract(synthetic)['selection'] is None)
        synthetic=copy.deepcopy(c);synthetic['parameters']={}
        with patch('placement_frontend.choose_placement',side_effect=AssertionError('absent domain reached optimizer')):
            check('referenced absent domain rejected',len(select_complete_contract(synthetic)['unresolved_parameters'])==2)
        synthetic=copy.deepcopy(c);synthetic['parameters']={k:[0,10] for k in c['parameters']}
        with patch('placement_frontend.choose_placement',return_value={'sentinel':'unchanged input'}) as choose:
            check('finite synthetic input forwarded unchanged',select_complete_contract(synthetic)['selection']=={'sentinel':'unchanged input'})
            choose.assert_called_once_with(synthetic)
        check('official jar digest',digest(JAR)==SHA);report['retained_assets'][str(JAR)]=SHA
        binary=WORK/'official-binary';original=binary/'BOOT-INF/classes'
        with zipfile.ZipFile(JAR) as jar:
            libs=[binary/json.loads(line[2:]) for line in jar.read('BOOT-INF/classpath.idx').decode().splitlines() if line]
            for member in jar.namelist():
                if member.startswith(('BOOT-INF/classes/','BOOT-INF/lib/')) and not member.endswith('/'):
                    if digest(binary/member)!=hashlib.sha256(jar.read(member)).hexdigest():raise AssertionError(member)
        check('entire runtime matches official jar and ordered classpath',True)
        cp=os.pathsep.join(map(str,[original,*libs]));classes=out/'classes';classes.mkdir();empty=out/'empty';empty.mkdir()
        run([JDK/'javac','--release','17','-proc:none','-implicit:none','-sourcepath',empty,'-cp',cp,'-d',classes,probe],'compile.log')
        run([JDK/'javap','-cp',cp,'-c','-p','alfio.repository.PurchaseContextFieldRepository'],'field-bytecode.txt')
        run([JDK/'java','-Xmx256m','-XX:ActiveProcessorCount=2','-cp',str(classes)+os.pathsep+cp,
             'research.alfio.AlfioFieldContractProbe',out],'probe.log')
        binary_report=json.loads((out/'binary-probe.json').read_text())
        origin=urlparse(binary_report['origin'])
        check('probe executed official repository default methods',origin.scheme=='file' and not origin.netloc and Path(unquote(origin.path)).resolve()==original.resolve())
        check('binary behavioral checks passed',binary_report['status']=='passed')
        save('conclusion.json',dict(status='formula_recovered_domains_and_lowering_unresolved',
            binary_cases=len(binary_report['cases']),binary_checks=len(binary_report['checks']),
            formula='2 * additional_attendees + additional_mutations',
            remaining_unknown_phase_costs=sum(n.get('java_calls') is None for f in enriched.values() for n in f['nodes']),
            unresolved_domains=result['unresolved_parameters'],selected_global_plan=None,
            limits=['proxy abstract query methods; no real JDBC, DB effect or transaction proof',
                    'manual source summary and conservative host frames; no automatic fine-grained SSA or alias proof',
                    'Event branch only; subscription branch not validated',
                    'cost is successful-path issued calls, not affected rows, bytes, latency or exceptional-path cost',
                    'no capability deletion or executable optimization; original algorithm unchanged']))
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report)
        save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']))))


if __name__=='__main__':main()
