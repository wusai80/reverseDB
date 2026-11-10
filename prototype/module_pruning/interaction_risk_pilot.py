"""Bounded executable contrasts for learned dependency interactions."""
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path

from shared_risk import loss_derivatives, sigmoid, solve

PROTOCOL = Path(__file__).with_name('interaction_risk_protocol.json')


def program(family, same_key, mode):
    key = 'A' if same_key else 'B'
    def read(identity, cell, slot, returned=False):
        return dict(id=identity, kind='read', key=cell, slot=slot, returned=returned)
    def write(identity, cell, amount, slot=None):
        return dict(id=identity, kind='write', key=cell, amount=amount, slot=slot, mode=mode)
    if family == 'staged_updates':
        events=[read('r1','A','u'),read('r2',key,'v'),write('w1','A','a','u'),write('w2',key,'b','v')]
        changed=['r1','w1','r2','w2']
    elif family == 'write_order':
        events=[write('w1','A','a'),write('w2',key,'b')]; changed=['w2','w1']
    elif family == 'read_observation':
        events=[read('r1','A','u',True),write('w1',key,'a')]; changed=['w1','r1']
    else:
        raise ValueError(family)
    return events, changed


def execute(events, order, initial, amounts):
    state=copy.deepcopy(initial); locals_={}; result=None
    by_id={e['id']:e for e in events}
    for identity in order:
        e=by_id[identity]
        if e['kind']=='read':
            locals_[e['slot']]=state[e['key']]['quantity']
            if e['returned']: result=locals_[e['slot']]
        else:
            amount=amounts[e['amount']]
            if e['mode']=='assign': value=amount
            else:
                old=locals_[e['slot']] if e['slot'] is not None else state[e['key']]['quantity']
                value=old+amount if e['mode']=='add' else max(old,amount)
            state[e['key']]['quantity']=value
    return dict(state=state,result=result)


def features(events, order):
    # Primitive IR access summaries; this does not inspect execution outcomes.
    def accesses(e):
        read={e['key']} if e['kind']=='read' or e['mode']!='assign' and e['slot'] is None else set()
        write={e['key']} if e['kind']=='write' else set()
        return read,write
    position={identity:i for i,identity in enumerate(order)}
    inversions=conflicts=crossed=0; pairs=list(itertools.combinations(events,2))
    for a,b in pairs:
        ar,aw=accesses(a);br,bw=accesses(b)
        conflict=bool(aw&(br|bw) or bw&ar)
        inverted=position[a['id']]>position[b['id']]
        inversions+=inverted;conflicts+=conflict;crossed+=inverted and conflict
    n=len(events); p=len(pairs)
    return dict(inversion_fraction=inversions/p,potential_conflict_fraction=conflicts/p,
                read_fraction=sum(e['kind']=='read' for e in events)/n,
                assignment_fraction=sum(e['kind']=='write' and e['mode']=='assign' for e in events)/n,
                returned_read_fraction=sum(e['kind']=='read' and e['returned'] for e in events)/n,
                crossed_conflict_fraction=crossed/p)


def fit(rows, names, penalty):
    groups={r['family'] for r in rows}
    weights=[1/sum(t['family']==r['family'] for t in rows) for r in rows]
    total=len(groups)
    prior=(1+sum(w*r['y'] for w,r in zip(weights,rows)))/(2+total)
    means=[sum(w*r['features'][name] for w,r in zip(weights,rows))/total for name in names]
    design=[[r['features'][name]-m for name,m in zip(names,means)] for r in rows]
    labels=[r['y'] for r in rows];beta=[0.]*len(names);intercept=math.log(prior/(1-prior))
    for iteration in range(51):
        loss,gradient,hessian=loss_derivatives(beta,design,labels,weights,intercept,penalty)
        if max(map(abs,gradient))<1e-10: break
        if iteration==50:raise RuntimeError('No convergence')
        direction=solve(hessian,gradient); descent=sum(g*d for g,d in zip(gradient,direction));step=1.
        for _ in range(25):
            candidate=[b-step*d for b,d in zip(beta,direction)]
            candidate_loss=loss_derivatives(candidate,design,labels,weights,intercept,penalty)[0]
            # Near the optimum, predicted descent can be below one float ULP.
            # Allow rounding error in objective comparison, not in gradient convergence.
            if candidate_loss<=loss-.0001*step*descent+8*math.ulp(loss):beta=candidate;break
            step*=.5
        else:raise RuntimeError(f'No descent: iteration={iteration}, gradient={max(map(abs,gradient))}, descent={descent}, loss={loss}')
    return dict(names=names,coefficients=beta,means=means,intercept=intercept,prior=prior,iterations=iteration,
                final_max_gradient=max(map(abs,gradient)))


def predict(model, values):
    return sigmoid(model['intercept']+sum(b*(values[n]-m) for n,b,m in zip(model['names'],model['coefficients'],model['means'])))


def run():
    protocol=json.loads(PROTOCOL.read_text());rows=[];programs={}
    for family in protocol['families']:
        for reorder,same_key,mode in itertools.product(*protocol['factors'].values()):
            events,changed=program(family,same_key,mode)
            order=changed if reorder else [e['id'] for e in events]
            identity=f'{family}/{int(reorder)}/{int(same_key)}/{mode}'
            f=features(events,order)
            for a,b in protocol['interactions']:f[a+'*'+b]=f[a]*f[b]
            rows.append(dict(id=identity,family=family,reordered=reorder,features=f))
            programs[identity]=(events,order)
    snapshot_hash=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    for row in rows:
        events,order=programs[row['id']];differences=0;witness=None;comparisons=0
        for x,y,a,b in itertools.product(protocol['states'],protocol['states'],protocol['amounts'],protocol['amounts']):
            state=dict(A=dict(quantity=x,metadata='keep-A'),B=dict(quantity=y,metadata='keep-B'),C=dict(quantity=71,metadata='unvisited'))
            amounts=dict(a=a,b=b)
            reference=execute(events,[e['id'] for e in events],state,amounts)
            candidate=execute(events,order,state,amounts)
            assert reference['state']['C']==candidate['state']['C']==state['C']
            assert state['A']['quantity']==x and state['B']['quantity']==y
            different=reference!=candidate;differences+=different;comparisons+=1
            if different and witness is None:witness=dict(initial=state,amounts=amounts,reference=reference,candidate=candidate)
        row.update(y=int(differences>0),comparisons=comparisons,differences=differences,witness=witness)
    names=protocol['base_features'];relational=names+['crossed_conflict_fraction']
    arms=dict(additive=names,relational=relational,interactions=relational+[a+'*'+b for a,b in protocol['interactions']])
    folds=[]
    for family in protocol['families']:
        train=[r for r in rows if r['family']!=family];test=[r for r in rows if r['family']==family]
        models={arm:fit(train,n,protocol['lambda']) for arm,n in arms.items()}
        prior=models['additive']['prior']; predictions=[]
        for row in test:
            scores=dict(training_prior=prior,**{arm:predict(m,row['features']) for arm,m in models.items()})
            predictions.append(dict(id=row['id'],y=row['y'],reordered=row['reordered'],scores=scores))
        def metrics(arm):
            return dict(brier=sum((r['scores'][arm]-r['y'])**2 for r in predictions)/len(test),
                        log_loss=-sum(r['y']*math.log(r['scores'][arm])+(1-r['y'])*math.log1p(-r['scores'][arm]) for r in predictions)/len(test))
        folds.append(dict(heldout_family=family,models=models,predictions=predictions,metrics={arm:metrics(arm) for arm in protocol['arms']}))
    macro={arm:{metric:sum(f['metrics'][arm][metric] for f in folds)/len(folds) for metric in ('brier','log_loss')} for arm in protocol['arms']}
    # Controls involve actual behavior, not labels manufactured from a feature threshold.
    lookup={r['id']:r for r in rows}
    assert all(not r['y'] for r in rows if not r['reordered'])
    assert lookup['write_order/1/1/add']['y']==0 and lookup['write_order/1/1/max']['y']==0
    assert lookup['write_order/1/1/assign']['y']==1
    assert all(not r['y'] for r in rows if '/1/0/' in r['id'])
    # Symbolic cell renaming must not change dependency features.
    for identity,(events,order) in programs.items():
        renamed=[dict(e,key='renamed_'+e['key']) for e in events]
        assert features(events,order)==features(renamed,order)
    # No lookup of a label is available to features(); retain snapshots for audit.
    return dict(scope=protocol['scope'],source_groups=1,feature_snapshot_sha256=snapshot_hash,rows=rows,folds=folds,macro_metrics=macro,
                summary=dict(candidates=len(rows),comparisons=sum(r['comparisons'] for r in rows),
                    changed_candidates=sum(r['y'] for r in rows),reordered_safe=sum(r['reordered'] and not r['y'] for r in rows),
                    reordered_unsafe=sum(r['reordered'] and r['y'] for r in rows)),
                checks=['identity schedules preserve behavior','shared-cell add/max commute','shared-cell assignments have counterexample',
                        'independent-cell reorderings preserve behavior','cell renaming preserves features','full state and untouched record retained'],
                source_representation='hand-authored primitive IR; no automatic extraction claim',new_database_executions=0,deletion_authorized=False)


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
