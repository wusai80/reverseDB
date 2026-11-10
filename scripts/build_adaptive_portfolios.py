"""Bind archived studies to the common planner; no new timing or deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.adaptive_planning.selector import select, fingerprint, POLICY
from placement_frontend import select_complete_contract

FOSSIL=ROOT/'evidence/fossil-hash-slice/20261110-v2'
TPCC=ROOT/'evidence/tpcc-contract-slice/20261110-72f5adabbcb5'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
OSCAR=ROOT/'evidence/oscar-ordered-handoff/20261108-d39fe138c91b'

def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return dict(path=str(p.relative_to(ROOT)),sha256=sha(p))


def build(out):
    out.mkdir(parents=True,exist_ok=False)
    save(out/'protocol.json',dict(policy=POLICY,
        scope='Retrospective development replay of known studies; not held-out adaptation or a fresh performance evaluation',
        candidate_families=['original','deletion','fusion','validated composition'],
        selection='Same semantic checkers; paired latency with conservative uncertainty and simpler-plan preference',
        composition='Requires joint validation, nonoverlapping regions and measured whole-plan cost; no summed savings',
        deployment='Recommendations only; no default application replacement'))
    engines=[ROOT/'prototype/module_pruning/demand_model.py',ROOT/'scripts/fusion_placement.py',
             ROOT/'scripts/fusion_regions.py',ROOT/'scripts/placement_frontend.py']
    for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
        assert sha(ROOT/'scripts'/name)==sha(FIRST/'inputs/scripts'/name)
    for freeze in ['paper/fossil-hash-slice-freeze.json','paper/tpcc-contract-slice-freeze.json']:
        for p,h in load(ROOT/freeze)['sha256'].items():assert sha(ROOT/p)==h,p
    catalogs={}
    def context(label,study,workload,source,contract,timing):
        return dict(label=label,source_digest=sha(source),contract_digest=fingerprint(contract),
                    workload_digest=fingerprint(workload),deployment_digest=fingerprint(dict(study=str(study.relative_to(ROOT)),protocol=sha(study/'protocol.json'))),
                    timing_boundary=timing,measurement_scope='Archived study only; remeasure after workload/deployment change')
    def portfolio(name,ctx):
        folder=out/name;folder.mkdir()
        p=dict(schema='adaptive-plan-portfolio/v1',context=ctx,engine_bindings=[ref(x) for x in engines],candidates=[],
               provenance='Study-specific adapter binds reviewed source summaries and actual complete-plan measurements')
        catalogs[name]=(p,folder);return p,folder
    def candidate(p,folder,name,family,artifacts,validation,regions,gates=None,components=None,values=None,blocks=None,cost_sources=None):
        ctx=fingerprint(p['context'])
        c=dict(id=name,family=family,context=ctx,authority='reviewed_experiment',artifacts=[ref(x) for x in artifacts],
               regions=regions,validation=dict(context=ctx,evidence=[ref(x) for x in validation]),gates=gates or [])
        if components:
            c['components']=components;c['validation']['components']=components
        if values is not None:
            record=dict(context=ctx,unit='ms',values=values,blocks=blocks)
            path=folder/(name+'-cost.json');save(path,record)
            c['cost']=dict(record,binding=ref(path),evidence=[ref(x) for x in cost_sources])
        p['candidates'].append(c)
    def demand_gate(folder,payload):
        path=folder/'demand-input.json';save(path,payload)
        return dict(kind='deletion',input=ref(path))
    def fusion_gate(folder,label,contract):
        expected=folder/(label+'-expected.json');save(expected,select_complete_contract(load(contract)))
        return dict(kind='fusion',input=ref(contract),expected=ref(expected))

    fossil_summary=load(FOSSIL/'summary.json');fp=load(FOSSIL/'protocol.json');slice_=load(FOSSIL/'dependency-slice.json')
    for scenario in fossil_summary['performance']:
        name='fossil-'+scenario['name']
        ctx=context(name,FOSSIL,dict(fixture=sha(FOSSIL/'fixtures/2000.fossil'),name=scenario['name']),
                    ROOT/'work/fossil/build-original/fossil',fp,'whole-process wall ms including startup and output capture')
        p,folder=portfolio(name,ctx)
        gate=demand_gate(folder,{k:slice_[k] for k in ['graph','facts','removed','obligations']})
        for arm in ['original','slice']:
            candidate(p,folder,arm,'original' if arm=='original' else 'deletion',
                [ROOT/'work/fossil/build-original/fossil'] if arm=='original' else [FOSSIL/'fossil-slice',FOSSIL/'slice.c'],
                [FOSSIL/'summary.json',FOSSIL.with_name(FOSSIL.name+'-audit.json')],
                [] if arm=='original' else ['timeline/presentation'],gates=[] if arm=='original' else [gate],
                values=[b['runs'][arm]['elapsed_ms'] for b in scenario['blocks']],blocks=list(range(len(scenario['blocks']))),
                cost_sources=[FOSSIL/'summary.json',FOSSIL/'manifest.json'])
    t=load(TPCC/'run.json');tp=load(TPCC/'protocol.json');demand=load(TPCC/'demand-decision.json')
    base=ROOT/'evidence/tpcc-roundtrip/20261103-9954559e225c/bundle'
    ctx=context('tpcc-mixed',TPCC,dict(protocol=tp['workload'],rows=599051),base/'reference.jar',tp,
                'sum of accepted business transaction intervals including commit/rollback; excludes startup and validation')
    p,folder=portfolio('tpcc-mixed',ctx)
    dg=demand_gate(folder,dict(graph=demand['graph'],facts=demand['facts'],obligations=demand['obligations'],
                              removed=demand['graph']['candidates']['display_module']))
    fg=[fusion_gate(folder,n,FIRST/(n+'-contract.json')) for n in ['neworder','payment']]
    paths={'original':base/'reference-adapter','slice_only':TPCC/'slice_only-classes',
           'fusion_only':TPCC/'combined-classes','fusion_slice':TPCC/'fusion_slice-classes'}
    regions={'original':[],'slice_only':['orderstatus/presentation'],
             'fusion_only':['neworder/body','payment/segments'],'fusion_slice':['orderstatus/presentation','neworder/body','payment/segments']}
    for arm,family in [('original','original'),('slice_only','deletion'),('fusion_only','fusion'),('fusion_slice','composition')]:
        records=sorted([r for r in t['cases'] if r['phase']=='measurement' and r['arm']==arm],key=lambda r:r['round'])
        artifacts=[paths[arm]/'com/oltpbenchmark/api/TpccApp.class',base/'reference.jar']
        if arm in ['fusion_only','fusion_slice']:
            artifacts += [paths[arm]/('com/oltpbenchmark/api/'+n+'.class') for n in ['FusedNewOrder','SegmentedPayment']]
            artifacts.append(TPCC/'generated/install.sql')
        candidate(p,folder,arm,family,artifacts,[TPCC/'run.json',TPCC.with_name(TPCC.name+'-audit.json')],regions[arm],
            gates=[dg] if arm=='slice_only' else fg if arm=='fusion_only' else [],
            components=['slice_only','fusion_only'] if arm=='fusion_slice' else None,
            values=[sum(s['duration_ns'] for s in r['samples'])/1e6 for r in records],blocks=[r['round'] for r in records],
            cost_sources=[TPCC/'run.json',TPCC/'manifest.json'])
    # Oscar's actual existing gate returns no backend region. It is NOT made
    # eligible by historical timing or by a synthetic positive cost estimate.
    screen=ROOT/'evidence/oscar-cross-layer-screen/20261107-c38bbd11addf'
    ctx=context('oscar-prefix',OSCAR,load(OSCAR/'protocol.json'),screen/'source-bytecode.json',
                load(screen/'prefix-contract.json'),'unmeasured here; existing admission boundary only')
    p,folder=portfolio('oscar-prefix',ctx)
    validation=[OSCAR/'run.json',OSCAR.with_name(OSCAR.name+'-audit.json')]
    candidate(p,folder,'original','original',[OSCAR/'inputs/prototype/oscar/baseline_probe.py'],validation,[])
    candidate(p,folder,'ordered_fusion','fusion',[OSCAR/'inputs/prototype/oscar/ordered_handoff_probe.py'],validation,
              ['analytics/prefix'],gates=[fusion_gate(folder,'oscar',screen/'prefix-contract.json')])
    results={}
    for name,(p,folder) in catalogs.items():
        save(folder/'catalog.json',p);results[name]=select(p,ROOT);save(folder/'selection.json',results[name])
    save(out/'summary.json',dict(status='passed',selections={k:v['selected'] for k,v in results.items()},
         retrospective=True,no_new_benchmarks=True,no_automatic_deployment=True))
    inputs=out/'inputs';inputs.mkdir()
    for path in [Path(__file__).resolve(),ROOT/'prototype/adaptive_planning/selector.py']:
        shutil.copyfile(path,inputs/path.name)
    save(out/'manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
    return results


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True,type=Path)
    results=build(ap.parse_args().out.resolve())
    print(json.dumps({k:dict(selected=v['selected'],frontier=v['frontier']) for k,v in results.items()}))
