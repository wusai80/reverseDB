#!/usr/bin/env python3
"""Finite research examples for deletion vs contextual replacement, not an analyzer."""
import copy
import hashlib
import itertools
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def outcome(state,result,status='ok',display=None):
    return dict(state=state,result=result,status=status,display=display)


def display_program(rows,delete=False,fault=False,observe_display=False):
    """Only this model's display SELECT may fail; final result reads full rows."""
    if not delete and fault: return outcome(rows,None,'query_error')
    rendered=None if delete else [str(x) for x in rows]
    return outcome(rows,list(rows),display=rendered if observe_display else None)


def posting_program(state,amount,delete=False):
    result=list(state)
    if not delete: result[0]+=amount  # Return value of posting is discarded.
    return outcome(result,'ok')


def step(quantity,amount):
    return quantity-amount if quantity-amount>=10 else quantity-amount+91


def staged_stock(state,lines):
    """Source model: all reads precede ordered absolute quantity assignments."""
    working=copy.deepcopy(state); pending=[]
    for item,amount in lines:
        if item not in working: return outcome(copy.deepcopy(state),None,'invalid_item')
        pending.append((item,amount,step(working[item]['quantity'],amount)))
    for item,amount,quantity in pending:
        working[item]['quantity']=quantity
        working[item]['ytd']+=amount
        working[item]['count']+=1
    return outcome(working,list(lines))


def fused_stock(state,lines):
    """Different implementation: derive final assignment and total deltas per key.

    Valid only for this serial pure-state model without observed intermediate
    updates/triggers; not a proposed new SQL backend or general batching rule.
    """
    if any(item not in state for item,_ in lines): return outcome(copy.deepcopy(state),None,'invalid_item')
    grouped={}
    for item,amount in lines: grouped.setdefault(item,[]).append(amount)
    working=copy.deepcopy(state)
    for item,amounts in grouped.items():
        working[item]=dict(quantity=step(state[item]['quantity'],amounts[-1]),
                           ytd=state[item]['ytd']+sum(amounts),count=state[item]['count']+len(amounts))
    return outcome(working,list(lines))


def eager_stock(state,lines):
    working=copy.deepcopy(state)
    for item,amount in lines:
        if item not in working: return outcome(copy.deepcopy(state),None,'invalid_item')
        row=working[item]
        row['quantity']=step(row['quantity'],amount); row['ytd']+=amount; row['count']+=1
    return outcome(working,list(lines))


def experiment():
    report=dict(scope='Finite abstract examples plus symbolic argument in companion note; not application or DB executions',
                complete_business_state_preserved=True,comparisons={},counterexamples={},checks=[])
    def check(name,value):
        if not value: raise AssertionError(name)
        report['checks'].append(name)
    rows=[list(x) for n in range(4) for x in itertools.product(range(3),repeat=n)]
    check('display deletion preserves full rows and result under normal unobserved-display contract',
          all(display_program(x)==display_program(x,delete=True) for x in rows))
    for name,kwargs in [('display_query_failure',dict(fault=True)),('display_becomes_required',dict(observe_display=True))]:
        differences=[dict(input=x,original=display_program(x,**kwargs),candidate=display_program(x,delete=True,**kwargs)) for x in rows
                     if display_program(x,**kwargs)!=display_program(x,delete=True,**kwargs)]
        check(name+' invalidates the former deletion contract',len(differences)==len(rows))
        report['counterexamples'][name]=differences[0]
    report['comparisons']['display']=dict(normal=len(rows),query_failure=len(rows),display_required=len(rows))
    postings=[(list(s),amount) for s in itertools.product(range(3),repeat=2) for amount in (1,2)]
    pairs=[(posting_program(s,a),posting_program(s,a,delete=True)) for s,a in postings]
    check('unused return does not justify deleting state writes',all(a['result']==b['result'] and a['state']!=b['state'] for a,b in pairs))
    report['counterexamples']['unused_return_with_write']=dict(input=postings[0],original=pairs[0][0],candidate=pairs[0][1])
    report['comparisons']['posting']=len(postings)
    states=[]
    for a,b,untouched in itertools.product((10,20),(10,20),(7,8)):
        states.append(dict(A=dict(quantity=a,ytd=4,count=2),B=dict(quantity=b,ytd=5,count=3),C=dict(quantity=untouched,ytd=6,count=4)))
    alphabet=list(itertools.product(('A','B'),(1,2,3)))
    orders=[list(x) for n in range(1,4) for x in itertools.product(alphabet,repeat=n)]
    correct=wrong=missed_by_response_only=0; witness=None
    for state in states:
        for order in orders:
            original=staged_stock(state,order); fused=fused_stock(state,order); eager=eager_stock(state,order)
            if original!=fused: raise AssertionError(('fusion mismatch',state,order))
            if not (original['state']['C']==fused['state']['C']==state['C']): raise AssertionError('Unvisited record changed')
            correct+=1
            if original!=eager:
                wrong+=1
                missed_by_response_only+=original['result']==eager['result']
                if witness is None: witness=dict(input=dict(state=state,lines=order),original=original,candidate=eager)
    check('finite complete-state fusion comparisons all agree',correct==len(states)*len(orders))
    check('premature writes change state while preserving returned order lines',wrong>0 and missed_by_response_only==wrong)
    report['counterexamples']['eager_write']=witness
    aborted=0
    for state in states:
        for order in [[('X',1)],[('A',1),('X',1)],[('A',1),('X',1),('B',2)]]:
            a,b,c=staged_stock(state,order),fused_stock(state,order),eager_stock(state,order)
            if not(a==b==c and a['status']=='invalid_item' and a['state']==state): raise AssertionError('Abort did not restore full state')
            aborted+=1
    check('invalid items preserve status and full rollback in this model',aborted==24)
    report['comparisons']['stock']=dict(states=len(states),orders=len(orders),valid_pairs=correct,abort_pairs=aborted,
      eager_write_counterexamples=wrong,response_only_missed=missed_by_response_only)
    report['baseline_failures']=dict(call_reachability='Executed display module is deletable under the stated contract; reachability alone retains it',
      discarded_return='Posting return is unused, but deleting its write changes full state',
      response_only='Premature stock writes can preserve returned lines while changing stored quantities',
      necessity_as_implementation_identity='Stock effects are necessary, but a different fused realization can preserve them')
    # Bind the ordering lesson to an already executed real five-line NewOrder
    # counterexample; these historical executions are not counted again.
    archive=ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013'
    p=archive/'probe-results.json'; manifest=json.loads((archive/'manifest.json').read_text())
    check('existing real counterexample artifact is intact',hashlib.sha256(p.read_bytes()).hexdigest()==manifest['probe-results.json'])
    real=json.loads(p.read_text())['early_write_counterexample']
    check('existing real five-line ordering counterexample remains discriminating',real['original']!=real['mutant'])
    report['existing_real_evidence']=dict(path=str(p.relative_to(ROOT)),sha256=manifest['probe-results.json'],witness=real,new_executions=0)
    return report


if __name__=='__main__':
    print(json.dumps(experiment(),ensure_ascii=False,indent=2))
