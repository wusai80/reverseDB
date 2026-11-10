"""Shared ordered-placement representation of a source-verified grouped lookup.

This builder does not recognize source patterns or authorize rewrites. Its caller
must supply the read/group/lookup semantics. Primitive key bounds follow JVM
value cardinality; they are conservative bounds, not observed list lengths.
"""
import copy


def key_cardinality(descriptor):
    bits={'Z':1,'B':8,'S':16,'C':16,'I':32,'J':64}.get(descriptor)
    return None if bits is None else 2**bits


def grouped_lookup_contract(key_descriptor, read_sql, lookup_sql, source):
    bound=key_cardinality(key_descriptor)
    tx='original_outer_spring_transaction'
    def node(name,uses,defines,cost,queries):
        return dict(id=name,transaction=tx,body_known=True,uses=uses,defines=defines,
                    java_calls=cost,database=None,queries=queries,
                    requires=['all_original_elements_and_reference_identity','original_group_and_effect_order','first_failure_stops_prefix',
                              'preserve_statement_snapshots_and_visibility',
                              'preserve_lookup_cardinality_failures',
                              'preserve_privilege_checks_on_empty_input',
                              'preserve_per_query_materialization_timing'],
                    retention_reason='Source summary only; no wire codec or admitted database lowering')
    nodes=[
        node('read_items',{'reservation_id':'java_string_reference','event':'original_event_reference','item_repository':'original_repository_proxy'},
             {'items':'complete_original_item_list'},dict(base=1,terms={}),[read_sql]),
        node('group_items',{'items':'complete_original_item_list'},
             {'groups':'original_group_map_of_complete_lists'},dict(base=0,terms={}),[]),
        node('lookup_definitions',{'groups':'original_group_map_of_complete_lists','event':'original_event_reference','service_repository':'original_repository_proxy'},
             {'pairs':'complete_pairs_with_original_item_references'},dict(base=0,terms={'groups':1}),[lookup_sql]),
        node('return_pairs',{'pairs':'complete_pairs_with_original_item_references'},
             {'method_result':'complete_pairs_with_original_item_references'},dict(base=0,terms={}),[])]
    # Conservative scope decision: preserve original query + host mapper as a
    # unit. Do not assume schema-valid rows always pass model construction.
    for n in nodes:
        if n['id'] in ('read_items','lookup_definitions'):
            n['host_retention']=dict(required=True,
                reason='Original per-query host materialization and failure timing are retained; relocation is not authorized',
                release_condition='new explicit scope decision plus evidence covering original mapping and exception behavior')
    nodes[2]['loop_effect_contract']=dict(
        order=['query_current_group','original_host_materialization','emit_current_pair','advance_to_next_group'],
        advance_requires='original_host_materialization_completed_successfully',
        on_mapping_failure='propagate_original_exception_without_querying_later_groups',
        implementation='retain original repository call and mapper; no replacement validator or JSON batch path',
        applicability='all original input rows, including schema-valid values rejected by the original mapper')
    edges=[dict(before='read_items',after='group_items',kind='value_and_effect_order',resource='items'),
           dict(before='group_items',after='lookup_definitions',kind='value_and_control',resource='groups'),
           dict(before='lookup_definitions',after='return_pairs',kind='value_and_effect_order',resource='pairs')]
    return dict(nodes=nodes,edges=edges,external_inputs={
        'reservation_id':'java_string_reference','event':'original_event_reference',
        'item_repository':'original_repository_proxy','service_repository':'original_repository_proxy'},
        parameters={'groups':None if bound is None else [0,bound]},wire_types={},max_boundary_values=None,
        source=copy.deepcopy(source),scope=dict(
            condition='normal completion of eager collection, original concrete objects and ordinary in-memory collections',
            groups='mathematical number of distinct key values, not potentially capped Map.size or number of item rows',
            key_descriptor=key_descriptor,domain_proof=None if bound is None else dict(
                upper=bound,basis='number of distinct values in the JVM primitive key type',tight=False,
                caveat='conservative type-domain overapproximation; not a claim all endpoint cardinalities are reachable in this application'),
            grouping='preserve every element occurrence, identity and encounter order within each group; no assumption of sorted group order',
            lazy_boundary='read and grouping happen when stream is built; definition queries occur when mapped results are evaluated (count may skip map); full 1+groups cost applies only to eager collector',
            limits='manual source semantics; no automatic grouping recognition, backend equivalence, concurrency or whole-program optimum'))
