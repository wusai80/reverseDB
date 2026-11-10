"""Fixed SQL-IR deltas for one source-bound statement replacement.

No labels or module names enter extraction. These are structural observations,
not hand-written equivalence/risk decisions. Unchanged Java is a scope premise.
"""
import collections
import json
from value_effect_ir import parse_sql, walk

FEATURES=['value_change_fraction','predicate_changed','ordering_changed',
          'limit_changed','parameter_use_change','numeric_literal_mass_delta']


def vector(reference, candidate):
    parsed=[parse_sql(x) for x in (reference,candidate)]
    if any(x['status']!='represented' for x in parsed):
        return dict(status='abstain',features=None,reasons=[x.get('reason') for x in parsed])
    a,b=[x['ir'] for x in parsed]
    if any(a[k]!=b[k] for k in ('kind','table','parameter_count','locking')):
        return dict(status='abstain',features=None,reasons=['statement kind/table/arity/lock correspondence changed'])
    def values(ir):
        return {**{('write',v['column']):v['value'] for v in ir['assignments']},
                **{('projection',v['position']):v for v in ir['projections']}}
    av,bv=values(a),values(b)
    if av.keys()!=bv.keys():return dict(status='abstain',features=None,reasons=['effect port inventory changed'])
    def uses(ir):
        return collections.Counter(t['slot'] for t in walk(ir) if t['op']=='jdbc_parameter')
    def numeric(ir):
        return sum(int(t['sql']) for t in walk(ir) if t['op']=='literal' and t['sql'].isdigit())
    au,bu=uses(a),uses(b)
    x=dict(value_change_fraction=sum(av[k]!=bv[k] for k in av)/max(1,len(av)),
           predicate_changed=int(a['predicate']!=b['predicate']),
           ordering_changed=int(a['ordering']!=b['ordering']),
           limit_changed=int(a['limit']!=b['limit']),
           parameter_use_change=sum(abs(au[k]-bu[k]) for k in au.keys()|bu.keys()),
           numeric_literal_mass_delta=abs(numeric(a)-numeric(b)))
    return dict(status='scorable_under_scope',features=x,
                scope='single SQL replacement; same Java source outside selected SQL literal; no semantic proof',
                risk_score=None,deletion_authorized=False)


def controls():
    checks=[]
    def check(name,value):assert value,name;checks.append(name)
    a='UPDATE ledger SET amount=amount+? WHERE id=?'
    z='UPDATE ledger SET amount=amount+(?+0) WHERE id=?'
    n='UPDATE ledger SET amount=amount+(?+1) WHERE id=?'
    check('identity is zero structural change',not any(vector(a,a)['features'].values()))
    check('added zero is retained as structural change',vector(a,z)['features']['value_change_fraction']==1)
    check('literal values supply distinct numerical input',vector(a,z)['features']['numeric_literal_mass_delta']==0 and vector(a,n)['features']['numeric_literal_mass_delta']==1)
    rename=lambda x:x.replace('ledger','other').replace('amount','payload').replace('id','key')
    check('consistent identifier renaming invariant',vector(a,n)['features']==vector(rename(a),rename(n))['features'])
    check('comments do not change features',vector(a,n)['features']==vector(a+' -- ? fake',n)['features'])
    check('unsupported entire statement abstains',vector('SELECT x FROM a,b','SELECT x FROM a,b')['status']=='abstain')
    check('changed parameter count abstains',vector(a,a.replace('id=?','id=1'))['status']=='abstain')
    q='SELECT value FROM ledger WHERE id=? ORDER BY value DESC LIMIT 1'
    check('ordering and limit remain separate',vector(q,q.replace('DESC','ASC'))['features']['ordering_changed']==1 and vector(q,q.replace('LIMIT 1','LIMIT 2'))['features']['limit_changed']==1)
    return dict(count=len(checks),checks=checks)


if __name__=='__main__':print(json.dumps(controls(),indent=2))
