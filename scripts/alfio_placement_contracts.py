"""Source-bound alf.io input adapter for the unchanged ordered-placement-dp/v1.

This module groups one method into semantic phases. It does not select regions,
change costs based on timings, or normalize unsupported SQL into supported SQL.
"""
import copy

REPOSITORY = 'alfio/repository/TicketRepository'


def reservation_contract(facts, source, caller_source, empty=False):
    # An arbitrary IntFunction may itself execute SQL. Bind the zero-call
    # preparation claim to the original in-memory attendee supplier call site.
    caller = next(c for c in facts['classes'] if c['class'] == 'alfio/manager/TicketReservationManager')
    supplier_target = 'alfio/manager/TicketReservationManager#getAtIndexOrEmpty(Ljava/util/List;I)Lalfio/model/modification/AttendeeData;'
    suppliers = [m for m in caller['methods'] if m['name'].startswith('lambda$initTicketsForReservation') and
                 [x['target'] for x in m['calls']] == [supplier_target]]
    if len(suppliers) != 1 or 'event.getVatStatus(), idx -> getAtIndexOrEmpty(attendees, idx)' not in caller_source:
        raise ValueError('Unknown attendee supplier call site; no zero-call preparation assumption')
    cls = next(c for c in facts['classes'] if c['class'] == REPOSITORY)
    methods = {m['name']: m for m in cls['methods'] if m['name'] in {'reserveTickets', 'batchReserveTickets'}}
    body = methods['reserveTickets']
    calls = [c['target'] for c in body['calls'] if c['kind'] == 'direct_call']
    materialize = 'java/util/stream/Stream#toArray(Ljava/util/function/IntFunction;)[Ljava/lang/Object;'
    batch = 'org/springframework/jdbc/core/namedparam/NamedParameterJdbcTemplate#batchUpdate(Ljava/lang/String;[Lorg/springframework/jdbc/core/namedparam/SqlParameterSource;)[I'
    fold = 'java/util/stream/LongStream#sum()J'
    if calls.count(batch) != 1 or not calls.index(materialize) < calls.index(batch) < calls.index(fold):
        raise ValueError('Unsupported original method structure')
    annotation = next(a for a in methods['batchReserveTickets']['annotations'] if a['type'] == 'Lch/digitalfondue/npjt/Query;')
    query = annotation['values']['value']
    if query not in source or 'Arrays.stream(this.getNamedParameterJdbcTemplate().batchUpdate(this.batchReserveTickets(), batchReserveParameters)).asLongStream().sum()' not in source:
        raise ValueError('Decompiled body does not match the archived boundary')
    tx = 'inherited_reservation_transaction'
    obligations = ['complete_ordered_parameters', 'same_outer_transaction', 'invoker_and_rls_context',
                   'ordered_updates_and_failure_prefix', 'affected_row_vector']
    nodes = [
        dict(id='materialize_parameters', body_known=True, transaction=tx,
             uses={'input': 'reservation_java_input'}, defines={'rows': 'ordered_complete_parameters'},
             queries=[], checks=['all supplier, name, metadata and VAT preparation completes before dispatch'],
             requires=['java_preparation_and_exceptions'], java_calls={'base': 0, 'terms': {}}, database=None,
             retention_reason='No database lowering for the original Java supplier and validation closure'),
        dict(id='execute_ordered_batch', body_known=True, transaction=tx,
             uses={'rows': 'ordered_complete_parameters'}, defines={'counts': 'jdbc_int_counts'},
             queries=[query], requires=obligations, java_calls={'base': 0 if empty else 1, 'terms': {}},
             database=dict(lowering='archived-alfio-ordered-batch-experiment', verified_source_contract=False,
                           guarantees=copy.deepcopy(obligations), transaction_policy='same_outer_transaction',
                           admission='bounded experiment only; diagnostic exception-chain differences remain; not a general frontend trust declaration')),
        dict(id='fold_and_return', body_known=True, transaction=tx,
             uses={'counts': 'jdbc_int_counts'}, defines={'result': 'java_int_result'}, queries=[],
             requires=['java_long_sum_and_int_narrowing'], java_calls={'base': 0, 'terms': {}}, database=None,
             retention_reason='Original Java aggregation retained; no separately admitted database lowering')]
    edges = [dict(before='materialize_parameters', after='execute_ordered_batch', kind='value_flow', resource='rows'),
             dict(before='materialize_parameters', after='execute_ordered_batch', kind='control_guard', resource='preparation_exceptions'),
             dict(before='execute_ordered_batch', after='fold_and_return', kind='value_flow', resource='counts')]
    return dict(nodes=nodes, edges=edges, external_inputs={'input': 'reservation_java_input'}, parameters={},
                wire_types={
                    'ordered_complete_parameters': dict(host_to_database=True, database_to_host=False, evidence='archived complete twelve-field payload'),
                    'jdbc_int_counts': dict(host_to_database=False, database_to_host=True, evidence='archived integer[] result transport')},
                max_boundary_values=None,
                contract_authority='Manual source-bound phase grouping plus archived bytecode/contract evidence; no automatic semantic proof',
                scope=dict(method=body['id'], branch='empty input' if empty else 'nonempty input, successful Java preparation, pinned PostgreSQL JDBC batch support',
                           caller='original ordinary initTicketsForReservation call site and inherited transaction',
                           java_input_assumptions='original model objects and in-memory collections; no arbitrary supplier, subtype, custom serializer or collection callback',
                           coverage='one Repository method only, not the whole reservation transaction',
                           cost='JDBC execute count, not per-row UPDATE count or parameter setter count',
                           input_size='no bound inferred from the 1/2/3-ticket test samples',
                           sql_policy='exact original annotation including named parameters, literal and casts; no SQL erasure or simplification'),
                source_evidence=dict(repository_class_sha256=cls['sha256'], query_method=methods['batchReserveTickets']['id'],
                                     caller_class_sha256=caller['sha256'], attendee_supplier=suppliers[0]['id'],
                                     ordered_direct_call_witness=[materialize, batch, fold]))
