"""Source-bound effect summary, not a selector or authorization to lower SQL."""
import copy
import hashlib

OWNER = 'alfio/repository/PurchaseContextFieldRepository'
SOURCE_SHA = '2db0ceabb0e914cbd6f674513fc30affd68dfd80805a4f00a5672e56392fe5c9'
CLASS_SHA = 'c8d92ff8d0b13642272fa76bb9b91307e6033244e01c92d8e79bf84b9aee1dc5'


def field_summary(facts, source):
    classes = {c['class']: c for c in facts['classes']}
    if classes[OWNER]['sha256'] != CLASS_SHA or hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA:
        raise ValueError('Unsupported field repository version for manual effect summary')
    methods = {m['id']: m for c in facts['classes'] for m in c['methods']}
    names = ['updateOrInsert', 'getFieldValueJson', 'findAllByTicketIdGroupedByName',
             'collectByName', 'lambda$updateOrInsert$0']
    selected = [m for m in classes[OWNER]['methods'] if m['name'] in names]
    if len(selected) != len(names):
        raise ValueError('Missing or ambiguous original field methods')
    query_names = {'findAdditionalFieldsForEvent', 'findAllForContextByTicketId',
                   'findAllByTicketId', 'updateValue', 'deleteValue', 'insertValue'}
    queries = []
    for m in selected:
        for call in m['calls']:
            target = methods.get(call['target'])
            if target and target['name'] in query_names:
                for annotation in target['annotations']:
                    if annotation['type'] == 'Lch/digitalfondue/npjt/Query;':
                        queries.append(dict(caller=m['id'], instruction=call['index'],
                                            method=target['id'], sql=annotation['values']['value']))
    if len(queries) != 6:
        raise ValueError('Event field SQL sites no longer match source summary')
    return dict(
        authority='manual source-bound summary, original binary control-flow probe; not a general automatic proof',
        scope='Event branch of updateOrInsert with original model objects, ordinary finite in-memory collections and normal completion',
        source_sha256=SOURCE_SHA, class_sha256=CLASS_SHA,
        methods=copy.deepcopy(selected), sql_sites=queries,
        invocation_cost=dict(base=2, terms={'field_mutations':1}),
        parent_loop_cost=dict(base=0, terms={'additional_attendees':2, 'additional_mutations':1}),
        parameter_definitions={
            'additional_attendees':'number of original reserved-ticket iterations whose attendee additional map is nonnull; empty map still counts',
            'additional_mutations':'number of UPDATE/DELETE/INSERT calls executed across those invocations, not affected rows'},
        finite_parameter_domains=None,
        domain_reason='No application-wide finite bounds established; observed ticket/map sizes are not legal-domain bounds',
        value_dependencies=[
            dict(stage='configuration_read',uses=['event.id'],defines=['ordered_complete_configurations']),
            dict(stage='existing_values_read',uses=['ticket_id','event.version'],defines=['complete_existing_values'],
                 alternatives=['findAllByTicketId','findAllForContextByTicketId(ATTENDEE)'],exactly_one=True),
            dict(stage='derive_maps',uses=['ordered_complete_configurations','complete_existing_values'],
                 defines=['existing_by_name','read_only_names','configuration_id_by_name'],
                 failure='duplicate existing/configuration names throw before any field mutation'),
            dict(stage='convert_each_value',uses=['original_map_iteration','entire_original_field_list'],defines=['scalar_or_json'],
                 rule='singleton raw element; otherwise JSON of complete list if any nonblank element, else empty string; null list throws even for unknown/readonly field'),
            dict(stage='choose_at_most_one_mutation',uses=['existing_by_name','read_only_names','configuration_id_by_name','scalar_or_json'],
                 alternatives=[
                     dict(when='existing and not readonly and nonblank',effect='updateValue',ids='stored value ticket/subscription/configuration IDs'),
                     dict(when='existing and not readonly and blank',effect='deleteValue',ids='stored value ticket/subscription/configuration IDs'),
                     dict(when='not existing and configured and nonblank (including readonly)',effect='insertValue',
                          ids='invocation ticket/subscription IDs, context organization ID, configuration map ID'),
                     dict(when='otherwise',effect='none')])],
        ordering=['parent ordered batch','parent complete-count guard',
                  'for each original reserved ticket: obtain attendee; test hasAdditionalData',
                  'if present: read configuration; read existing values; collect maps; process input entries in original iteration order',
                  'parent first-ticket read after entire loop'],
        errors='stop at first failure; earlier query/mutation calls remain in execution prefix; no assertion about rollback or external effects',
        safety='No data filtering, reordering, query hoisting, new wire codec or database lowering authorized; subscription branch outside this summary')


def attach_field_summary(flows, summary):
    """Refine one opaque host stage; leave the same optimizer and all edges intact."""
    result = copy.deepcopy(flows)
    flow = result['ordinary_initialization']
    node = next(n for n in flow['nodes'] if n['id'] == 'all_additional_fields')
    node['java_calls'] = copy.deepcopy(summary['parent_loop_cost'])
    node.pop('cost_unknown_reason', None)
    node['cost_basis'] = 'source-bound successful Event path: two reads per invoked attendee plus issued mutations; domains unresolved'
    node['queries'] = [q['sql'] for q in summary['sql_sites']]
    node['source']['field_effect_summary'] = copy.deepcopy(summary)
    flow['parameters'].update({name:None for name in summary['parameter_definitions']})
    flow['scope']['cost_parameter_definition'] = copy.deepcopy(summary['parameter_definitions'])
    return result
