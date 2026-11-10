"""Fixed old-value, alternative-definition and wrong-value Payment contrasts.

These are experimental candidates, not changes to the default application.
"""
import hashlib
from guarded_value_features import extract
from payment_order_variants import finish_body


def variants(helper, ddl):
    producer = 'if(bad) cust.c_data=(String)TpccApp.invoke(p,"getCData",c,1,d,cd,1,amount,cust);'
    assert helper.count(producer) == 1
    old_value = ('cust.c_data=reversedb.tpcc.AppRuntime.rows(c,'
                 '"SELECT c_data FROM customer WHERE c_w_id=? AND c_d_id=? AND c_id=?",'
                 '1,cd,cust.c_id).getJSONObject(0).getString("c_data");')
    helpers = dict(full=helper,
                   old_value=helper.replace(producer, old_value+'\n    '+producer),
                   alternative_producer=helper.replace(producer, producer+'\n    if(!bad) '+old_value),
                   aligned_empty=helper.replace(producer, 'if(bad) cust.c_data="";'))
    left, right, body = finish_body(ddl)
    old_update = 'C_PAYMENT_CNT = p_count WHERE'
    assert body.count(old_update) == 1
    both = body.replace(old_update, 'C_PAYMENT_CNT = p_count, C_DATA = p_data WHERE')
    bodies = dict(full=body, old_value=both, alternative_producer=both, aligned_empty=body)
    ddls = {name: ddl[:left]+candidate+ddl[right:] for name, candidate in bodies.items()}
    features = {name: extract(helpers[name], ddls[name]) for name in helpers}
    assert features['full']['status'] == features['aligned_empty']['status'] == 'extracted_under_scope'
    assert features['full']['features'] == features['aligned_empty']['features']
    for name in ('old_value', 'alternative_producer'):
        assert features[name]['status'] == 'unknown' and features[name]['features'] is None
    return dict(helpers=helpers, bodies=bodies, ddls=ddls, features=features,
                source_sha256={name: dict(helper=hashlib.sha256(helpers[name].encode()).hexdigest(),
                                          ddl=hashlib.sha256(ddls[name].encode()).hexdigest()) for name in helpers},
                limits=['unchanged extractor; unknown is not unsafe or a zero risk score',
                        'old-value variants write the same GC data and keep original BC computation',
                        'aligned_empty retains guard alignment while destroying the BC payload',
                        'fixed sequential execution; no claim about concurrency, triggers or arbitrary programs',
                        'no model fitting, scores, automatic deletion or data reduction'])
