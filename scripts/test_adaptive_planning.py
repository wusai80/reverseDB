"""Safety, composition and adaptive-choice controls; no application benchmarks."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.adaptive_planning.selector import select, POLICY, fingerprint

def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def ref(p):return dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def main():
    ap=argparse.ArgumentParser();ap.add_argument('study',type=Path);ap.add_argument('--out',required=True,type=Path)
    args=ap.parse_args();study=args.study.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    baseline=load(study/'tpcc-mixed/catalog.json');checks=[];counter=0;results={}
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    def plan(catalog):return select(catalog,ROOT)
    def byid(c,name):return next(x for x in c['candidates'] if x['id']==name)
    def record(value):
        nonlocal counter
        counter+=1;p=out/f'control-{counter}.json';save(p,value);return ref(p)
    def costs(c,values):
        for name,value in values.items():
            cost=byid(c,name)['cost'];cost['values']=[float(value)]*len(cost['blocks'])
            payload={k:cost[k] for k in ['context','unit','values','blocks']}
            cost['binding']=record(payload);cost['evidence']=[cost['binding']]
        return c
    def simulated(values):return costs(copy.deepcopy(baseline),values)
    for label,wanted in [('fossil-hash','slice'),('fossil-branch','slice'),('tpcc-mixed','fusion_only'),('oscar-prefix','original')]:
        result=plan(load(study/label/'catalog.json'));check('archive replay '+label,result['selected']==wanted);results[label]=result
    profiles=[('deletion best',dict(original=100,slice_only=60,fusion_only=90,fusion_slice=80),'slice_only'),
              ('fusion best',dict(original=100,slice_only=90,fusion_only=60,fusion_slice=59.8),'fusion_only'),
              ('joint plan best',dict(original=100,slice_only=90,fusion_only=75,fusion_slice=50),'fusion_slice'),
              ('no useful gain',dict(original=100,slice_only=99,fusion_only=100,fusion_slice=101),'original')]
    for name,values,wanted in profiles:
        c=simulated(values);r=plan(c);check('synthetic '+name,r['selected']==wanted)
        c['candidates'].reverse();check('candidate input order invariant '+name,plan(c)['selected']==wanted)
    fast=dict(original=100,slice_only=85,fusion_only=65,fusion_slice=30)
    def mutate(name,change,target='fusion_slice',legal=False):
        c=simulated(fast);change(c);r=plan(c)
        check(name,r['selected']!=target)
        if not legal:check(name+' ineligible',r['admission'][target]['status']=='ineligible')
        return r
    mutate('composition needs its own validation',lambda c:byid(c,'fusion_slice')['validation'].pop('components'))
    mutate('component order is bound',lambda c:byid(c,'fusion_slice')['validation'].update(components=['fusion_only','slice_only']))
    mutate('overlap requires reanalysis',lambda c:byid(c,'slice_only').update(regions=['neworder/body']))
    mutate('missing component rejected',lambda c:byid(c,'fusion_slice').update(components=['absent','fusion_only']))
    mutate('composition cycle rejected',lambda c:byid(c,'fusion_slice').update(components=['fusion_slice','fusion_only']))
    mutate('joint cost cannot be summed',lambda c:byid(c,'fusion_slice').pop('cost'),legal=True)
    mutate('different contract rejected',lambda c:byid(c,'fusion_slice').update(context='different'))
    mutate('unknown family rejected',lambda c:byid(c,'fusion_slice').update(family='magic_optimizer'))
    mutate('unpaired blocks rejected',lambda c:byid(c,'fusion_slice')['cost'].update(blocks=list(range(7))),legal=True)
    mutate('duplicate blocks rejected',lambda c:byid(c,'fusion_slice')['cost'].update(blocks=[0]*8),legal=True)
    mutate('different timing unit rejected',lambda c:byid(c,'fusion_slice')['cost'].update(unit='jdbc_calls'),legal=True)
    mutate('different workload cost rejected',lambda c:byid(c,'fusion_slice')['cost'].update(context='other-workload'),legal=True)
    mutate('unbound favorable numbers rejected',lambda c:byid(c,'fusion_slice')['cost'].update(values=[0.1]*8),legal=True)
    for value in [0,-1,float('inf'),float('nan')]:
        mutate('invalid latency '+str(value),lambda c,v=value:byid(c,'fusion_slice')['cost'].update(values=[v]*8),legal=True)
    c=simulated(fast);byid(c,'original').pop('cost')
    check('no baseline cost retains original',plan(c)['selected']=='original')
    def bad_validation(c):
        byid(c,'fusion_slice')['validation']['evidence']=[record(dict(status='failed'))]
    mutate('failed joint validation rejected',bad_validation)
    def stale(c):
        p=out/'changed-artifact';p.write_text('before');binding=ref(p);p.write_text('after')
        byid(c,'fusion_slice')['artifacts']=[binding]
    mutate('changed binary rejected',stale)
    c=simulated(fast);byid(c,'original')['artifacts'][0]['sha256']='0'*64
    try:plan(c)
    except ValueError:check('invalid original cannot become fallback',True)
    else:check('invalid original cannot become fallback',False)
    for mode in ['required','unknown']:
        c=simulated(dict(original=100,slice_only=1,fusion_only=80,fusion_slice=0.1))
        gate=byid(c,'slice_only')['gates'][0];payload=load(ROOT/gate['input']['path'])
        if mode=='required':payload['facts']['legacy_failure_possible']=True
        else:payload['facts'].pop('legacy_failure_possible')
        gate['input']=record(payload);r=plan(c)
        check(mode+' deletion cannot win on speed',r['admission']['slice_only']['status']=='ineligible' and r['admission']['fusion_slice']['status']=='ineligible')
        check(mode+' valid alternative remains',r['selected']=='fusion_only')
    c=load(study/'oscar-prefix/catalog.json');bad=byid(c,'ordered_fusion')
    bad['cost']=dict(byid(baseline,'fusion_only')['cost'],values=[0.0001]*8)
    check('Oscar unknown legality cannot be rescued by cost',plan(c)['admission']['ordered_fusion']['status']=='ineligible')
    c=simulated(fast);c['engine_bindings'][0]['sha256']='0'*64
    try:plan(c)
    except ValueError:check('changed shared engine rejected',True)
    else:check('changed shared engine rejected',False)
    c=simulated(fast);c['engine_bindings']=[]
    try:plan(c)
    except ValueError:check('missing checker bindings rejected',True)
    else:check('missing checker bindings rejected',False)
    c=simulated(fast);c['candidates']=[x for x in c['candidates'] if x['id']!='fusion_slice']
    result=plan(c)
    check('joint candidate proposed without fabricated approval',len(result['composition_opportunities'])==1
          and result['composition_opportunities'][0]['status']=='needs_joint_construction_validation_and_measurement'
          and not result['composition_opportunities'][0]['permits_execution'] and result['selected']=='fusion_only')
    # Identical context policy, but clear new paired evidence reverses choice.
    a=simulated(dict(original=100,slice_only=90,fusion_only=60,fusion_slice=59.8))
    b=costs(copy.deepcopy(a),dict(original=100,slice_only=45,fusion_only=80,fusion_slice=75))
    check('evidence changes strategy without project-specific logic',plan(a)['selected']=='fusion_only' and plan(b)['selected']=='slice_only')
    c=simulated(dict(original=1e200,slice_only=1e-200,fusion_only=1e100,fusion_slice=1e150))
    check('finite extreme costs do not overflow',plan(c)['selected']=='slice_only')
    save(out/'summary.json',dict(status='passed',checks=checks,retrospective_selections={k:v['selected'] for k,v in results.items()},
        synthetic_profiles='Control evidence only; not new application timing or held-out validation',automatic_deployment=False))
    save(out/'manifest.json',{str(p.relative_to(out)):ref(p)['sha256'] for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
    print(json.dumps(dict(status='passed',checks=len(checks))))


if __name__=='__main__':main()
