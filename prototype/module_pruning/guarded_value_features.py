"""One source-bound guarded-value relation, not a safety classifier."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/'evidence/payment-order-e2e/20261106-a57ab6b102d2'


def mask_java(source):
    return re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: ''.join('\n' if c=='\n' else ' ' for c in m.group()),source)


def mask_sql(source):
    return re.sub(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'",
                  lambda m: ''.join('\n' if c=='\n' else ' ' for c in m.group()),source)


def only(matches,reason):
    if len(matches)!=1:raise ValueError(reason)
    return matches[0]


def extract(helper,ddl):
    """Selected slots 8/9 of the frozen Payment finish call; simple Boolean guards.

    Slot correspondence/normal call target are explicit scope, not discovered
    whole-program behavior. Reports execution of a selected assignment, not
    whether the resulting value is nonnull or semantically valid.
    """
    try:
        java=mask_java(helper);sql=mask_sql(ddl)
        payload=only(list(re.finditer(r'\b(\w+)\.setString\(8,\s*(\w+\.\w+)\s*\)',java)), 'nonunique_or_unsupported_payload_binding')
        flag=only(list(re.finditer(r'\b(\w+)\.setBoolean\(9,\s*(!?)\s*(\w+)\s*\)',java)), 'nonunique_or_unsupported_flag_binding')
        receiver,field=payload[1],payload[2];guard=flag[3]
        if receiver!=flag[1]:raise ValueError('different_statement_receivers')
        only(list(re.finditer(r'\bboolean\s+'+re.escape(guard)+r'\s*=',java)), 'missing_primitive_boolean_declaration')
        if len(re.findall(r'\b'+re.escape(guard)+r'\s*=(?!=)',java))!=1:raise ValueError('boolean_reassigned')
        assignment=only(list(re.finditer(r'(?m)^\s*if\s*\(\s*(!?)\s*'+re.escape(guard)+r'\s*\)\s*'+re.escape(field)+r'\s*=(?!=)([^;]+);',java)), 'unsupported_assignment_guard')
        if len(re.findall(re.escape(field)+r'\s*=(?!=)',java))!=1:raise ValueError('multiple_selected_assignments')
        if assignment.end()>payload.start() or assignment.end()>flag.start():raise ValueError('producer_not_before_bindings')
        signature=only(list(re.finditer(r'CREATE FUNCTION reversedb_payment\.finish_payment\((.*?)\)\s*RETURNS',sql,re.S)), 'missing_selected_function')
        parameters=[p.strip().split() for p in signature[1].split(',')]
        if len(parameters)!=12 or parameters[7][1:]!=['text'] or parameters[8][1:]!=['boolean']:
            raise ValueError('unsupported_parameter_signature')
        data_parameter,flag_parameter=parameters[7][0],parameters[8][0]
        start=sql.index('$body$',signature.end())+len('$body$');end=sql.index('$body$',start);body=sql[start:end]
        branch=only(list(re.finditer(r'(?ms)^IF\s+(NOT\s+)?'+re.escape(flag_parameter)+r'\s+THEN\s*\n(.*?)^ELSE\s*\n(.*?)^END IF;',body)), 'unsupported_consumer_guard')
        use_pattern=r'\bC_DATA\s*=\s*'+re.escape(data_parameter)+r'\b'
        true_uses=len(re.findall(use_pattern,branch[2],re.I));false_uses=len(re.findall(use_pattern,branch[3],re.I))
        if true_uses+false_uses!=1 or len(re.findall(r'\b'+re.escape(data_parameter)+r'\b',body))!=1:
            raise ValueError('unsupported_payload_consumers')
        valuations=[]
        for value in (False,True):
            produced=(not value) if assignment[1] else value
            bound=(not value) if flag[2] else value
            take_then=(not bound) if branch[1] else bound
            consumed=bool(true_uses if take_then else false_uses)
            valuations.append(dict(symbolic_guard=value,assignment_executed=produced,sql_flag=bound,consumer_executed=consumed))
        counters=dict(consumer_without_assignment=0,assignment_without_consumer=0,both_active=0,both_inactive=0)
        for v in valuations:
            a,c=v['assignment_executed'],v['consumer_executed']
            key='both_active' if a and c else 'assignment_without_consumer' if a else 'consumer_without_assignment' if c else 'both_inactive'
            counters[key]+=1
        return dict(status='extracted_under_scope',features={k+'_fraction':v/2 for k,v in counters.items()},
                    valuations=valuations,
                    binding=dict(payload_slot=8,flag_slot=9,payload_expression=field,guard_local=guard,
                                 sql_payload=data_parameter,sql_guard=flag_parameter),
                    source_positions=dict(producer_line=helper.count('\n',0,assignment.start())+1,
                                          payload_binding_line=helper.count('\n',0,payload.start())+1),
                    assumptions=['frozen helper structure and selected finish call; no extra surrounding path constraints',
                                 'normal completion of selected conditional assignment; no unmodeled alias mutation before bind',
                                 'primitive Java boolean sent by setBoolean; SQL flag cannot be NULL in this call path',
                                 'earlier field value and validity of assigned value are not established'],
                    score=None,deletion_authorized=False)
    except (ValueError,IndexError) as error:
        return dict(status='unknown',reason=str(error),features=None,score=None,deletion_authorized=False)


def run():
    manifest=json.loads((ARCHIVE/'manifest.json').read_text());sources={}
    def read(name):
        p=ARCHIVE/name;data=p.read_bytes();h=hashlib.sha256(data).hexdigest();assert manifest[name]==h
        sources[name]=h;return data.decode()
    helper=read('generated/SegmentedPayment.java')
    names=['full','history_projection_swap','stale_projection','inverted_credit_guard']
    rows={name:extract(helper,read('generated/'+name+'-install.sql')) for name in names}
    assert all(r['status']=='extracted_under_scope' for r in rows.values())
    # Snapshot relation features before opening the archived runtime labels.
    feature_snapshot=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    base_sql=read('generated/full-install.sql');inverted_sql=read('generated/inverted_credit_guard-install.sql')
    controls={}
    def check(name,value):
        assert value,name;controls[name]=True
    base=rows['full']['features'];different=rows['inverted_credit_guard']['features']
    check('real guard inversion creates a relation distinction',base!=different)
    check('renaming linked symbols leaves numerical features unchanged',
          extract(re.sub(r'\bbad\b','credit_flag',helper),re.sub(r'\bp_bad\b','flag_arg',re.sub(r'\bp_data\b','payload_arg',base_sql)))['features']==base)
    check('binding negation and consumer negation compose',extract(helper.replace('setBoolean(9,bad)','setBoolean(9,!bad)'),inverted_sql)['features']==base)
    check('negating producer alone changes relation',extract(helper.replace('if(bad) cust.c_data','if(!bad) cust.c_data'),base_sql)['features']==different)
    check('negating producer and consumer together preserves alignment only',extract(helper.replace('if(bad) cust.c_data','if(!bad) cust.c_data'),inverted_sql)['features']==base)
    check('fake producer in comment ignored',extract(helper+'\n// if(other) cust.c_data=null;\n',base_sql)['features']==base)
    for name,j,s in [
        ('compound producer',helper.replace('if(bad) cust.c_data','if(bad && customer>0) cust.c_data'),base_sql),
        ('compound consumer',helper,base_sql.replace('IF p_bad THEN','IF p_bad AND p_count>0 THEN')),
        ('duplicate payload binding',helper.replace('s.setString(8,cust.c_data);','s.setString(8,cust.c_data); s.setString(8,cust.c_data);'),base_sql),
        ('flag reassignment',helper.replace('s.setBoolean(9,bad);','bad=false; s.setBoolean(9,bad);'),base_sql),
        ('unsupported flag expression',helper.replace('setBoolean(9,bad)','setBoolean(9,bad && customer>0)'),base_sql)]:
        result=extract(j,s);check(name+' remains unknown',result['status']=='unknown' and result['features'] is None)
    labels=json.loads(read('candidate-labels.json'));old=json.loads(read('preexecution-features.json'))
    combined={name:dict(inversions=old['features'][name]['inverted_pairs'],
                        table_conflicts=old['features'][name]['inverted_table_conflicts'],
                        **rows[name]['features']) for name in names}
    check('prior full/guard collision separated without fitting',old['features']['full']==old['features']['inverted_credit_guard'] and combined['full']!=combined['inverted_credit_guard'])
    return dict(scope='One selected guarded assignment/value-consumption relation; not a probability or safety model',source_hashes=sources,
                feature_snapshot_sha256=feature_snapshot,relations=rows,combined_features=combined,
                existing_batch_labels={name:labels[name]['observed_suite_difference'] for name in names},controls=controls,
                interpretation='Fractions weight two symbolic Boolean valuations equally; not request frequencies or failure probabilities. Relation alignment does not establish value validity or whole-program equivalence.',
                source_binding='Frozen generated Payment helper and finish SQL; explicit slots and selected C_DATA use, not general automatic dependency recovery',
                new_business_executions=0,new_model_fits=0,training_admitted=False)


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
