"""Fixed Payment contrasts; reuse the existing SQL schedule features unchanged."""
import hashlib
from real_order_features import extract, compare


def finish_body(ddl):
    start=ddl.index('CREATE FUNCTION reversedb_payment.finish_payment(')
    left=ddl.index('$body$',start)+len('$body$')
    right=ddl.index('$body$',left)
    return left,right,ddl[left:right]


def variants(ddl):
    left,right,body=finish_body(ddl);lines=body.splitlines()
    projections=[i for i,line in enumerate(lines) if line.startswith('SELECT to_jsonb(r) INTO v_result FROM ')]
    histories=[i for i,line in enumerate(lines) if line.startswith('INSERT INTO history ')]
    assert len(projections)==len(histories)==1
    projection,history=projections[0],histories[0]
    assert history<projection and body.count('IF p_bad THEN')==1
    legal=list(lines);legal[history],legal[projection]=legal[projection],legal[history]
    stale=list(lines);statement=stale.pop(projection);stale.insert(stale.index('BEGIN')+1,statement)
    bodies=dict(full=body,history_projection_swap='\n'.join(legal)+'\n',
                stale_projection='\n'.join(stale)+'\n',inverted_credit_guard=body.replace('IF p_bad THEN','IF NOT p_bad THEN',1))
    ddls={name:ddl[:left]+candidate+ddl[right:] for name,candidate in bodies.items()}
    reference=extract(body)
    features={name:compare(reference,extract(candidate),1) for name,candidate in bodies.items()}
    assert features['full']['inverted_pairs']==0
    assert features['history_projection_swap']['inverted_pairs']==1
    assert features['history_projection_swap']['inverted_table_conflicts']==0
    assert features['stale_projection']['inverted_table_conflicts']==2
    assert features['inverted_credit_guard']==features['full']
    return dict(ddls=ddls,bodies=bodies,features=features,
                body_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in bodies.items()},
                limits=['unchanged schedule features do not represent IF predicates',
                        'two customer-update branches are mutually exclusive; conflict counts are potential static pairs, not simultaneous executions',
                        'no model fitting or scoring; known contrasts selected before this execution'])
