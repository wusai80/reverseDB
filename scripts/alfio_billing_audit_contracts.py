"""Source-bound billing/audit effects, with the original inter-query host clock."""
import copy


def billing_audit_contract(parent, methods, date_constructor):
    phases={n['id']:n for n in parent['nodes']}
    prep,update,audit=[phases[k] for k in ['billing_java_values','update_billing','audit_creation']]
    method=methods[update['source']['method']]
    instructions=method['instructions'];by_index={i['index']:i for i in instructions}
    update_site=update['source']['calls'][0]
    clock=next(c for c in audit['source']['calls'] if c['target']=='java/util/Date#<init>()V')
    audit_site=audit['source']['calls'][-1]
    if not update_site['index']<clock['index']<audit_site['index']:raise ValueError('Changed clock ordering')
    for call in [update_site,clock,audit_site]:
        if by_index[call['index']].get('target')!=call['target']:raise ValueError('Changed binary call anchor')
    if method['exception_handlers']:raise ValueError('New handler requires failure-path analysis')
    span=[i for i in instructions if update_site['index']<=i['index']<=audit_site['index']]
    if any(i['opcode'] in range(153,178) or i['opcode'] in (191,198,199,200,201) for i in span):
        raise ValueError('Unexpected control transfer inside effect interval')
    for call in [update_site,audit_site]:
        following=next(i for i in instructions if i['index']>call['index'])
        if following['opcode']!=87:raise ValueError('Affected-row result now used')
    if 'java/lang/System.currentTimeMillis:()J' not in date_constructor:
        raise ValueError('Unverified JDK host-clock constructor')
    tx=update['transaction'];required=['same_original_transaction','original_invoker_and_rls',
        'full_original_parameters_and_data','original_failure_prefix_and_exception_mapping',
        'original_jdbc_converters_and_count_semantics']
    def node(name,uses,defines,cost,requirements,query=None,retain=None):
        n=dict(id=name,uses=uses,defines=defines,body_known=True,transaction=tx,
               java_calls=dict(base=cost,terms={}),database=None,queries=[] if query is None else [query],
               requires=requirements,retention_reason='No verified replacement; original Java/NPJT/JDBC execution retained')
        if retain:n['host_retention']=dict(required=True,reason=retain)
        return n
    nodes=[
        node('prepare_billing',{'event':'original_event_reference','total':'original_total_price_reference','reservation':'original_string_reference'},
             {'billing_args':'complete_original_billing_arguments'},0,
             ['original_getter_order','java_int32_wraparound_and_abs_min_value','original_null_and_failure_behavior'],
             retain='Keep original Java argument evaluation and numeric semantics'),
        node('update_billing',{'billing_args':'complete_original_billing_arguments','reservation_repo':'original_repository_proxy'},
             {},1,required,update['queries'][0]),
        node('prepare_audit_after_update',{'event':'original_event_reference','reservation':'original_string_reference'},
             {'audit_args':'complete_original_audit_arguments'},0,
             ['original_host_wall_clock','clock_read_after_successful_update_return','clock_read_before_audit_dispatch',
              'preserve_original_date_reference_and_milliseconds','no_clock_read_on_update_failure'],
             retain='Original host clock read and argument evaluation between the two database executions'),
        node('insert_audit',{'audit_args':'complete_original_audit_arguments','audit_repo':'original_repository_proxy'},
             {},1,required,audit['queries'][0])]
    edges=[dict(before=a['id'],after=b['id'],kind='source_order_and_failure_prefix',resource='original_effect_order')
           for a,b in zip(nodes,nodes[1:])]
    edges += [dict(before='prepare_billing',after='update_billing',kind='value_flow',resource='billing_args'),
              dict(before='prepare_audit_after_update',after='insert_audit',kind='value_flow',resource='audit_args')]
    contract=dict(nodes=nodes,edges=edges,external_inputs=dict(event='original_event_reference',total='original_total_price_reference',
                  reservation='original_string_reference',reservation_repo='original_repository_proxy',audit_repo='original_repository_proxy'),
                  parameters={},wire_types={},max_boundary_values=None,
                  contract_authority='manual original-source argument/effect summary anchored to pinned binary instructions; no new backend',
                  scope=dict(method=method['id'],condition='original billing/audit interval reached, original model instances and repository proxies',
                             transaction='inherit original caller transaction; no commit, rollback or savepoint introduced',
                             not_assumed='no frozen-clock domain restriction, no database/host clock equivalence, no guaranteed one-row UPDATE',
                             limits='typed argument bundles remain original Java values; no wire codec or whole-method completion proof'),
                  source=dict(class_sha256=parent['source_class_sha256'],update=update_site,clock=clock,audit=audit_site,
                              covered_instruction_interval=[prep['source']['instruction_start'],audit_site['index']+2]))
    bindings=dict(
        authority='manual reading of original source, same getter order retained; not a replacement expression evaluator',
        billing=[['vatStatus','event.getVatStatus()'],['srcPriceCts','calculateSrcPrice(captured vatStatus, totalPrice)'],
                 ['finalPriceCts','totalPrice.getPriceWithVAT()'],['vatCts','totalPrice.getVAT()'],
                 ['discountCts','Math.abs(totalPrice.getDiscount())'],['currencyCode','event.getCurrency()'],
                 ['vatNr',None],['vatCountry',None],['invoiceRequested',False],['reservationId','original reservationId reference']],
        audit=[['reservationId','original reservationId reference'],['userId',None],['eventId','Integer.valueOf(event.getId()) after UPDATE'],
               ['eventType','RESERVATION_CREATE'],['eventTime','new java.util.Date() after UPDATE returns'],
               ['entityType','RESERVATION'],['entityId','same reservationId reference'],['modifications',None]],
        arithmetic='Java int32 operations: (captured vatStatus == INCLUDED ? priceWithVAT : priceWithVAT - VAT) + Math.abs(discount); MIN_VALUE abs and overflow unchanged',
        counts='both primitive int affected-row returns are discarded; zero does not skip audit',
        failure='argument failure stops before UPDATE; UPDATE failure stops before host clock and audit; audit failure propagates to original transaction owner')
    enriched=copy.deepcopy(parent)
    parent_audit=next(n for n in enriched['nodes'] if n['id']=='audit_creation')
    parent_audit['host_retention']=copy.deepcopy(nodes[2]['host_retention'])
    return contract,bindings,enriched
