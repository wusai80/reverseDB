"""Pinned Event collector summary using the shared grouped-lookup schema."""
import copy
import hashlib
from grouped_lookup_contracts import grouped_lookup_contract

OWNER='alfio/manager/support/reservation/ReservationCostCalculator'
CLASS_SHA='b1432dc0640b6da129f9fa5b99546646dc754a0022ad1e32d4b15b367c57ded7'
SOURCE_SHA='312fc706349071decfca9db7b9dff61e725deb426140db8a4b5ac3bbc39a532b'
ITEM_SHA='ef3d52375dc7a55c4d717f5338dd3f39a4f99540317db8e5064c62406d318853'


def service_group_contract(facts,bytecode,source):
    classes={c['class']:c for c in bytecode['classes']}
    if (classes[OWNER]['sha256']!=CLASS_SHA or hashlib.sha256(source.encode()).hexdigest()!=SOURCE_SHA
            or classes['alfio/model/AdditionalServiceItem']['sha256']!=ITEM_SHA):
        raise ValueError('Unsupported source or binary for grouped service semantics')
    methods={m['id']:m for m in classes[OWNER]['methods']}
    entry=next(m['id'] for m in methods.values() if '#collectAdditionalServiceItems(' in m['id'])
    pending=[entry];seen=set();sites=[]
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current)
        for instruction in methods[current]['instructions']:
            targets=[]
            if 'target' in instruction:targets.append(('direct_call',instruction['target']))
            targets += [('bootstrap_handle',h['target']) for h in instruction.get('bootstrap_handles',[])]
            for kind,target in targets:
                sites.append(dict(method=current,instruction=instruction['index'],kind=kind,target=target))
                if target in methods:pending.append(target)
    getter='alfio/model/AdditionalServiceItem#getAdditionalServiceId()I'
    if not any(s['target']==getter and s['kind']=='bootstrap_handle' for s in sites):
        raise ValueError('Original primitive key getter not found')
    read='alfio/repository/AdditionalServiceItemRepository#findByReservationUuid(ILjava/lang/String;)Ljava/util/List;'
    lookup='alfio/repository/AdditionalServiceRepository#getById(II)Lalfio/model/AdditionalService;'
    repo_sites=[s for s in sites if s['target'].startswith('alfio/repository/')]
    if sorted(s['target'] for s in repo_sites)!=sorted([read,lookup]):
        raise ValueError('Collector query sites changed')
    original={m['id']:m for c in facts['classes'] for m in c['methods']}
    def query(target):
        annotations=[a for a in original[target]['annotations'] if a['type']=='Lch/digitalfondue/npjt/Query;']
        if len(annotations)!=1:raise ValueError('Missing original SQL annotation')
        return annotations[0]['values']['value']
    evidence=dict(method=entry,source_sha256=SOURCE_SHA,class_sha256=CLASS_SHA,
                  methods=[copy.deepcopy(methods[m]) for m in sorted(seen)],call_sites=sites,
                  key_getter=getter,query_sites=repo_sites,
                  authority='manual source-bound eager grouping semantics; call/handle sites extracted automatically; not automatic loop inference')
    return grouped_lookup_contract(getter.rsplit(')',1)[1],query(read),query(lookup),evidence)


def attach_service_group(flows,contract):
    result=copy.deepcopy(flows)
    result['service_group_collection']=copy.deepcopy(contract)
    parent=next(n for n in result['reservation_entry']['nodes'] if n['id']=='reservation_total')
    # This is one conditional subcall; do not mistake a subtotal for the cost of
    # the entire calculator (context, tickets, discount/subscriptions and prices).
    parent['source']['known_subcalls']=[dict(child='service_group_collection',
        condition='purchaseContext.event() present in ReservationCostCalculator; eager collectAdditionalServiceItems',
        cost=dict(base=1,terms={'groups':1}),parent_cost_still_unknown=True)]
    link=dict(parent='reservation_entry',node='reservation_total',child='service_group_collection',
              kind='Event-conditional subcall through ReservationCostCalculator; remainder unresolved')
    return result,link
