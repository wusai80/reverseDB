"""Manual ordered phases bound to frozen method instructions and call sites.

Ranges account for bytecode calls, not automatic proofs of values/aliases or
branch reachability. Unknown stages stay explicit and keep their actual SQL.
"""
import copy
import hashlib
from alfio_placement_contracts import reservation_contract

OWNER = 'alfio/manager/TicketReservationManager'


def build_flow_contracts(facts, repository_source, manager_source):
    classes = {c['class']: c for c in facts['classes']}
    bindings=[(classes[OWNER]['sha256'],'615052a54a712c73ec195b01bf7fc93136b4815a906b4472bad4abd1002e348f'),
              (classes['alfio/repository/TicketRepository']['sha256'],'670690f392ec21c7c02c725362c7c513a52ccab0ae7ad8f93fa7b45e7caebed3'),
              (hashlib.sha256(manager_source.encode()).hexdigest(),'f5912cb262484b8da2a47e629ffd0972108597f628613527f237163f2ee6a89b'),
              (hashlib.sha256(repository_source.encode()).hexdigest(),'d1cdcb27d7537c03654da338a89dd4833d02e60ffca42b9e87b2d38043f39759')]
    if any(actual!=expected for actual,expected in bindings):raise ValueError('Unsupported source version for manual instruction ranges')
    methods = {m['id']: m for c in facts['classes'] for m in c['methods']}
    manager = classes[OWNER]

    def method(name, descriptor_prefix=None, string_return=False):
        found = [m for m in manager['methods'] if m['name'] == name and
                 (descriptor_prefix is None or m['descriptor'].startswith(descriptor_prefix)) and
                 (not string_return or m['descriptor'].endswith('Ljava/lang/String;'))]
        if len(found) != 1: raise ValueError('Ambiguous source method ' + name)
        return found[0]

    def anchor(m, owner, name):
        sites = [c for c in m['calls'] if c['target'].startswith(owner + '#' + name + '(')]
        if len(sites) != 1: raise ValueError('Ambiguous source anchor ' + owner + '#' + name)
        return sites[0]['index']

    def query_sites(calls):
        result=[]
        for call in calls:
            target = methods.get(call['target'])
            if target:
                for a in target['annotations']:
                    if a['type'] == 'Lch/digitalfondue/npjt/Query;':
                        result.append(dict(method=target['id'], sql=a['values']['value'],
                                           annotation_type=a['values'].get('type'), instruction=call['index']))
        return result

    # The state frame is a conservative host-side dependency carrier, not a new
    # runtime object or wire codec. Fine-grained value/alias recovery remains open.
    def flow(name, m, definitions, coverage='all method call sites', condition='normal completion of this method'):
        nodes=[]; edges=[]; assigned=[]
        for i, spec in enumerate(definitions):
            ident, start, stop, cost, reason = spec
            calls = [copy.deepcopy(c) for c in m['calls'] if c['index'] >= start and (stop is None or c['index'] < stop)]
            sites = query_sites(calls); assigned.extend(c['index'] for c in calls)
            n=dict(id=ident, transaction='original_outer_spring_transaction', body_known=True,
                   uses={f'frame_{i}':'opaque_java_frame'}, defines={f'frame_{i+1}':'opaque_java_frame'},
                   java_calls=None if cost is None else copy.deepcopy(cost) if isinstance(cost,dict) else dict(base=cost,terms={}),
                   database=None, queries=[q['sql'] for q in sites], requires=['preserve_original_phase_and_full_effects'],
                   retention_reason='No admitted database lowering or complete value ABI for this phase',
                   source=dict(method=m['id'], instruction_start=start, instruction_stop_exclusive=stop,
                               calls=calls, direct_sql_annotations=sites))
            if cost is None: n['cost_unknown_reason']=reason
            else: n['cost_basis']=reason
            nodes.append(n)
            if i: edges.append(dict(before=nodes[i-1]['id'],after=ident,kind='source_order_and_conservative_value_flow',resource=f'frame_{i}'))
        if len(assigned)!=len(set(assigned)):raise ValueError('Overlapping phase call coverage')
        expected = [c['index'] for c in m['calls'] if coverage == 'all method call sites' or c['index'] >= definitions[0][1]]
        if sorted(assigned)!=sorted(expected):raise ValueError('Missing source calls: '+name)
        return dict(nodes=nodes,edges=edges,external_inputs={'frame_0':'opaque_java_frame'},parameters={},wire_types={},max_boundary_values=None,
                    contract_authority='manual source-bound phase/cost summaries; same shared DP schema; no lowering authorization',
                    scope=dict(name=name,method=m['id'],condition=condition,coverage=coverage,
                               source_order='whole phases; optional branches/loops remain inside the phase, not reordered or summed as unconditional calls',
                               frame='conservative host state dependency; no invented transport or detailed alias proof',
                               inputs='original model objects, original NPJT/Spring implementation and ordinary in-memory collections'),
                    coverage=dict(call_sites=len(assigned),expected_call_sites=len(expected),all_accounted_for=True),
                    source_class_sha256=manager['sha256'])

    root=method('createTicketReservation',string_return=True)
    header=anchor(root,'alfio/repository/TicketReservationRepository','createNewReservation')
    billing=anchor(root,'alfio/repository/TicketReservationRepository','updateBillingData')
    flows={}
    flows['reservation_entry']=flow('reservation_entry',root,[
        ('identity_and_promotion_lookup',0,14,dict(base=0,terms={'promotion_code_present':1}),'one query iff the input promotion Optional is present; original UUID and Event getters are host-only'),
        ('dynamic_discount',14,23,None,'dynamic discount branch and extension effects/cost are not closed'),
        ('promotion_usage_guard',23,31,None,'optional promotion limits and conditional SQL; not an unconditional constant'),
        ('header_argument_preparation',31,header,None,'retrievePublicUserId branches need a source-bound call-cost summary'),
        ('insert_reservation',header,header+1,1,'one original NPJT SQL INSERT invocation, normal path'),
        ('all_categories',header+1,101,None,'whole category loop; cardinality and branch costs not closed; see category child'),
        ('additional_services',101,110,None,'external manager booking path not summarized; empty fixture does not remove capability'),
        ('reservation_total',110,116,None,'ReservationCostCalculator delegate and query paths not summarized'),
        ('billing_java_values',116,billing,0,'original Event/TotalPrice getters, calculateSrcPrice integer arithmetic and Math.abs; no custom subtype callbacks'),
        ('update_billing',billing,billing+1,1,'one original NPJT SQL UPDATE invocation, normal path'),
        ('audit_creation',billing+1,164,None,'AuditingRepository default-method delegation not yet cost-closed in this adapter'),
        ('discount_limit_check',164,180,None,'conditional discount queries and TransactionTemplate scope need closure'),
        ('payment_applicability_and_return',180,None,None,'configuration and payment-manager dependencies, including failure branch')])
    promotion=method('lambda$createTicketReservation$0')
    expected_promotion=[OWNER.replace('manager/TicketReservationManager','model/Event')+'#getId()I',
                        'alfio/repository/PromoCodeDiscountRepository#findPromoCodeInEventOrOrganization(ILjava/lang/String;)Ljava/util/Optional;']
    if [c['target'] for c in promotion['calls']]!=expected_promotion or promotion['branch_targets']:
        raise ValueError('Promotion callback no longer a single original query')
    if 'promotionCodeDiscount.flatMap(' not in manager_source:raise ValueError('Missing original Optional branch')
    flows['reservation_entry']['parameters']={'promotion_code_present':[0,1]}
    flows['reservation_entry']['scope']['cost_parameter_definition']={'promotion_code_present':'1 iff input promotionCodeDiscount Optional is present, else 0; not inferred from fixture or timing'}
    promo_node=flows['reservation_entry']['nodes'][0]
    promo_sites=query_sites(promotion['calls'])
    if len(promo_sites)!=1:raise ValueError('Missing promotion SQL annotation')
    promo_node['queries']=[q['sql'] for q in promo_sites]
    promo_node['source']['conditional_callback']=dict(method=promotion['id'],calls=promotion['calls'],sql_annotations=promo_sites)
    category=method('reserveTicketsForCategory')
    reread=anchor(category,'alfio/repository/TicketCategoryRepository','getByIdAndActive')
    readticket=anchor(category,'alfio/repository/TicketRepository','findById')
    writeprice=anchor(category,'alfio/repository/TicketRepository','updateTicketPrice')
    flows['category_body']=flow('category_body',category,[
        ('access_tokens',0,55,None,'access and special-price branches have different queries/locks'),
        ('select_locked_tickets',55,85,2,'one category lookup plus exactly one of two mutually exclusive locking SELECTs; see locked_selection child'),
        ('quantity_guard',85,103,0,'original sizes, integer comparison and exception construction'),
        ('reread_category',103,reread+1,1,'one NPJT getByIdAndActive plus original scalar argument getters'),
        ('initialize_tickets',reread+1,128,None,'special/ordinary branches and full additional-field loop; see ordinary_initialization child'),
        ('read_first_ticket',128,readticket+1,1,'one NPJT findById and original list/scalar getters, on successful path'),
        ('java_price_values',readticket+1,writeprice,None,'TicketPriceContainer and MonetaryUtil transitive bodies not in frozen extracted class set'),
        ('write_ticket_prices',writeprice,None,1,'one original NPJT UPDATE with complete ID list')])
    locked=method('reserveTickets','(IIILjava/util/List;)')
    flows['locked_selection']=flow('locked_selection',locked,[
        ('category_lookup',0,7,1,'one original NPJT getByIdAndActive'),
        ('required_status_values',7,22,0,'in-memory enum-name mapping and original category predicate'),
        ('choose_and_lock',22,None,1,'bounded/unbounded branches are exclusive and each executes one SELECT; never sum both SQL sites')])
    init=method('initTicketsForReservation')
    flows['ordinary_initialization']=flow('ordinary_initialization',init,[
        ('ordinary_batch_and_arguments',280,297,1,'original nonempty ordinary batch; helper getters and captured in-memory attendee supplier; see batch child'),
        ('complete_count_guard',297,315,0,'Validate.isTrue against complete reserved ticket count'),
        ('all_additional_fields',315,None,None,'whole attendee loop with conditional updateOrInsert; no bound or no-effect inference from empty tables')],
        coverage='ordinary else-branch call sites from instruction 280; special-price branch retained outside this child',
        condition='specialPrices.isEmpty(), nonempty selected ticket list, original attendee supplier; parent list lookup and branch predicate retained outside child')
    flows['ordinary_batch']=reservation_contract(facts,repository_source,manager_source,empty=False)
    flows['empty_batch_boundary']=reservation_contract(facts,repository_source,manager_source,empty=True)
    links=[dict(parent='reservation_entry',node='all_categories',child='category_body',kind='repeated whole child; unknown repetition count/cost'),
           dict(parent='category_body',node='select_locked_tickets',child='locked_selection',kind='one normal-path child, exact total cost 2'),
           dict(parent='category_body',node='initialize_tickets',child='ordinary_initialization',kind='one guarded branch only; special branch unresolved'),
           dict(parent='ordinary_initialization',node='ordinary_batch_and_arguments',child='ordinary_batch',kind='nonempty ordinary call site, total cost 1')]
    return flows,links
