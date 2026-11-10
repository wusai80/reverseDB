"""Manual source-bound capability catalog, separate from name-independent DP.

No desired region list is supplied to the optimizer. Lowering/codec evidence is
reused from existing experiments; this module does not discover semantic facts.
"""
import copy
from fusion_regions import SOURCE_SHA, ADAPTER_SHA
from payment_fusion_transfer import SOURCE_SHA as PAYMENT_SHA


def wire(types):
    return {t:dict(host_to_database=True,database_to_host=True,evidence='existing source-bound JDBC ABI') for t in types}


def capability(rule,requirements):
    return dict(lowering=rule,verified_source_contract=True,guarantees=list(requirements),
                transaction_policy='same_outer_transaction')


def base(graph,inputs,parameters,wires):
    return dict(nodes=copy.deepcopy(graph['nodes']),edges=copy.deepcopy(graph['edges']),external_inputs=inputs,
                parameters=parameters,wire_types=wires,max_boundary_values=None,
                contract_authority='Manual source/phase/ABI summaries and archived differential evidence; not machine-proved semantics')


def neworder_contract(graph):
    binding=graph.get('source_contract',{})
    if binding.get('source_sha256')!=SOURCE_SHA or binding.get('adapter_sha256')!=ADAPTER_SHA:
        raise ValueError('Unsupported NewOrder contract')
    rules={
        'response_id':['initial_id_and_missing_row'], 'getCustomer':['customer_check'],
        'getWarehouse':['warehouse_check'],'getDistrict':['locked_id_read'],
        'updateDistrict':['increment_and_row_check'],'insertOpenOrder':['fixed_clock_and_bindings'],
        'insertNewOrder':['bindings'],
        'stage_lines':['all_input_reads_before_writes','float32_product','stock_arithmetic','ten_district_cases'],
        'flush_lines':['ordered_complete_batch'],'flush_stock':['ordered_complete_batch'],
        'result_order':['complete_order_output'],'result_lines':['complete_ordered_lines_output']}
    expected=set(rules)|{'input_validation','commit_or_rollback_and_return'}
    if {n['id'] for n in graph['nodes']}!=expected: raise ValueError('Unexpected source units')
    c=base(graph,dict(warehouse='int32',district='int32',customer='int32',lines='json',time='timestamp'),
           dict(line_count=[5,15]),wire(['int32','timestamp','json']))
    for n in c['nodes']:
        # Strip old placement decisions. Java originals remain alternatives.
        for field in ('placement','eligible','barriers'): n.pop(field,None)
        n['requires']=rules.get(n['id'],['outer_application_boundary'])
        n['database']=capability('existing-neworder/'+n['id'],n['requires']) if n['id'] in rules else None
        n['java_calls']=dict(base=1 if n['id'] in rules else 0,terms={})
        if n['id']=='stage_lines': n['java_calls']=dict(base=0,terms={'line_count':2})
        n['retention_reason']='Original input validation / transaction lifecycle'
    c['evidence']='evidence/neworder-fusion/20261105-4c76a7feb013'
    # ordered_staged_lines intentionally has no wire codec: it is legal inside
    # a whole DB region or the Java original, but cannot be exported at a cut.
    return c


def payment_contract(graph,source_sha):
    if source_sha!=PAYMENT_SHA: raise ValueError('Unsupported Payment source')
    rules={
        'updateWarehouse':['exact_jdbc_numeric','row_count_check'],
        'getWarehouse':['original_columns','text_name_result'],
        'updateDistrict':['exact_jdbc_numeric','row_count_check'],
        'getDistrict':['original_columns','text_name_result'],
        'selected_customer_update':['java_selected_credit_branch','exact_jdbc_float8','exact_jdbc_text','row_count_check'],
        'insertHistory':['exact_jdbc_float8','java_utf16_history','fixed_clock'],
        'result':['complete_customer_projection']}
    expected=set(rules)|{'java_amount_conversion','original_customer_and_credit_java','outer_commit_or_rollback'}
    if {n['id'] for n in graph['nodes']}!=expected: raise ValueError('Unexpected source units')
    c=base(graph,dict(w='int32',d='int32',cd='int32',c='int32',numeric='jdbc_numeric',amount='jdbc_float8',time='timestamp'),
           dict(bad_credit=[0,1]),wire(['int32','timestamp','json','jdbc_numeric','jdbc_float8','jdbc_text','jdbc_boolean']))
    for n in c['nodes']:
        for field in ('placement','eligible','barriers'): n.pop(field,None)
        n['requires']=rules.get(n['id'],['original_java_or_transaction_semantics'])
        n['database']=capability('existing-payment/'+n['id'],n['requires']) if n['id'] in rules else None
        n['java_calls']=dict(base=1 if n['id'] in rules else 0,terms={})
        n['retention_reason']='No verified database implementation for this Java/transaction unit'
        if n['id']=='original_customer_and_credit_java':
            n['requires']=['java_float32_arithmetic','java_utf16_strings','mutable_customer_identity','credit_predicate']
            n['java_calls']=dict(base=1,terms={'bad_credit':1})
        if n['id']=='java_amount_conversion': n['requires']=['bigdecimal_to_java_float32']
    c['evidence']='evidence/payment-segments/20261105-707221611457'
    return c
