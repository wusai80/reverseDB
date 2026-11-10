"""Payment transfer audit: reuse generic effects/regions without inventing lowerings.

The adapter's conditional block is kept opaque. Source-bound semantic units are
manual summaries; SQL and reflected targets/bindings come from attributed facts.
"""
import hashlib
import re
from fusion_regions import analyze, sql_effect

SOURCE_SHA = 'a375528861d4549b5973de8ff259b2a479bc6126098dd14e06daf78bf811cb44'
ADAPTER_SHA = '32a302f441e7a42a609b32ff836cdcbbacca8c5f3a225e43c04816d7b69cde99'


def extract(source, adapter, facts):
    if hashlib.sha256(source.encode()).hexdigest()!=SOURCE_SHA or hashlib.sha256(adapter.encode()).hexdigest()!=ADAPTER_SHA:
        raise ValueError('Unsupported Payment source/adapter')
    sql={r['symbol'].split('#')[1]:dict(sql=' '.join(r['constant_sql'].split()),source=r['location'])
         for r in facts['sql_fields'] if '.Payment#' in r['symbol']}
    if len(sql)!=10: raise ValueError('Incomplete Payment SQL inventory')
    calls=[r for r in facts['calls'] if '.Payment#' in r['caller']]
    variable_fields=dict(re.findall(r'PreparedStatement (\w+) = this\.getPreparedStatement\(conn, this\.(\w+)\)',source))
    methods={}
    for call in calls:
        method=call['caller'].split('#')[1].split('(')[0]
        match=re.fullmatch(r'(\w+)\.set(Int|Double|String|Timestamp|BigDecimal)\((\d+), (.*)\)',call['expression'],re.S)
        if match and match[1] in variable_fields:
            entry=methods.setdefault(method,dict(field=variable_fields[match[1]],bindings=[]))
            if entry['field']!=variable_fields[match[1]]: raise ValueError('Multiple SQL sites need a richer summary')
            entry['bindings'].append(dict(index=int(match[3]),jdbc_type=match[2],expression=match[4],source=call['location']))
    for method,entry in methods.items():
        expected=list(range(1,sql[entry['field']]['sql'].count('?')+1))
        if sorted(b['index'] for b in entry['bindings'])!=expected: raise ValueError('Incomplete bindings '+method)
    start=adapter.index('      case "Payment" -> {'); end=adapter.index('      case "OrderStatus" -> {',start)
    site_calls=[r for r in facts['calls'] if '.TpccApp#reference(' in r['caller']
                and start<=r['location']['start']<end and r.get('reflection',{}).get('requested_name')]
    inventory=[]
    for call in site_calls:
        transfer=call.get('reflection_argument_transfer',{})
        name=call['reflection']['requested_name']
        if transfer.get('status')!='verified_bridge_target' or not transfer.get('body_available'):
            raise ValueError('Missing attributed source target '+name)
        if name not in methods: raise ValueError('No SQL bindings for '+name)
        inventory.append(dict(method=name,target=transfer['target'],arguments=transfer['bindings'],
                              conditions=call['conditions'],source=call['location'],**methods[name]))
    expected=['updateWarehouse','getWarehouse','updateDistrict','getDistrict','getCustomerById',
              'getCData','updateBalanceCData','updateBalance','insertHistory']
    if [s['method'] for s in inventory]!=expected: raise ValueError('Unsupported adapter call structure')
    guarded=[s for s in inventory if s['conditions']['unknowns']]
    if {s['method'] for s in guarded}!={'getCData','updateBalanceCData','updateBalance'}:
        raise ValueError('Unexpected unresolved control structure')
    for s in guarded:
        if any(u.get('expression')!='(cust.c_credit.equals("BC"))' for u in s['conditions']['unknowns']):
            raise ValueError('Unexpected branch condition')
    projection=re.findall(r'rows\(c,"([^"]+)"',adapter[start:end])
    if len(projection)!=1: raise ValueError('Unexpected result consumer')
    return dict(kind='Payment-transfer-audit/v1',source_sha256=SOURCE_SHA,adapter_sha256=ADAPTER_SHA,
                sql=sql,call_sites=inventory,result_query=projection[0],
                unresolved_control='cust.c_credit.equals("BC"); both arms retained as one opaque unit',
                backend='none: no float/Customer/UTF-16/conditional lowering admitted')


def plan(ir):
    if ir.get('source_sha256')!=SOURCE_SHA or ir.get('adapter_sha256')!=ADAPTER_SHA:
        raise ValueError('Unsupported source contract')
    external=dict(warehouse='int32',district='int32',customer_district='int32',customer='int32',amount='java_bigdecimal',time='timestamp')
    nodes=[dict(id='input_validation',placement='host',body_known=True,transaction=None,queries=[],uses=external,defines={})]
    by_name={r['method']:r for r in ir['call_sites']}
    def add(id,names=(),uses=None,defines=None,known=False,obligations=(),queries=None):
        nodes.append(dict(id=id,placement='database',transaction='outer_serializable',body_known=known,
                          queries=queries if queries is not None else [ir['sql'][by_name[n]['field']]['sql'] for n in names],
                          uses=uses or {},defines=defines or {},checks=[id+' source checks'] if names else [],
                          source=[by_name[n]['source'] for n in names],open_obligations=list(obligations)))
    add('amount_conversion',uses={'amount':'java_bigdecimal'},defines={'payment_float':'java_float32'},
        obligations=['BigDecimal.floatValue rounding; one shared amount value for all consumers'])
    add('updateWarehouse',['updateWarehouse'],uses=dict(warehouse='int32',payment_float='java_float32'),
        obligations=['float32 -> float64 -> BigDecimal.valueOf JDBC binding','zero row failure'])
    add('getWarehouse',['getWarehouse'],uses={'warehouse':'int32'},defines={'warehouse_record':'java_warehouse'},
        obligations=['Warehouse object/string fields and missing-row failure'])
    add('updateDistrict',['updateDistrict'],uses=dict(warehouse='int32',district='int32',payment_float='java_float32'),
        obligations=['same monetary conversion as warehouse','zero row failure'])
    add('getDistrict',['getDistrict'],uses=dict(warehouse='int32',district='int32'),defines={'district_record':'java_district'},
        obligations=['District object/string fields and missing-row failure'])
    add('getCustomerById',['getCustomerById'],uses=dict(warehouse='int32',customer_district='int32',customer='int32'),
        defines={'customer_before':'java_customer'},obligations=['TPCCUtil.newCustomerFromResults external helper','getFloat fields, object identity, missing row'])
    add('customer_arithmetic',uses=dict(customer_before='java_customer',payment_float='java_float32'),defines={'customer_after':'java_customer'},
        obligations=['float32 balance and YTD arithmetic','Java int32 increment','same mutable customer consumed by later steps'])
    add('credit_branch',['getCData','updateBalanceCData','updateBalance'],
        uses=dict(customer_after='java_customer',payment_float='java_float32',warehouse='int32',district='int32',customer_district='int32'),
        defines={'customer_written':'java_customer'},obligations=['unresolved credit predicate; mutually exclusive branches, not a linear sequence',
            'Float.toString concatenation and UTF-16 substring(0,500)','typed setters and update count checks'])
    add('insertHistory',['insertHistory'],uses=dict(warehouse_record='java_warehouse',district_record='java_district',customer_written='java_customer',
                                                  payment_float='java_float32',warehouse='int32',district='int32',customer_district='int32',time='timestamp'),
        obligations=['UTF-16 name truncation and concatenation','float32 widened to JDBC double','frozen clock binding'])
    add('result_customer',uses=dict(customer_district='int32',customer='int32'),defines={'result':'json'},known=True,queries=[ir['result_query']])
    nodes.append(dict(id='commit_or_rollback_and_return',placement='host',body_known=True,transaction=None,queries=[],uses={'result':'json'},defines={}))
    graph=analyze(nodes,external)
    graph['coverage']=dict(sql_sites=sum(len(n['queries']) for n in nodes),
                           sql_effects_known=all(sql_effect(q)['known'] for n in nodes for q in n['queries']),
                           full_transaction_admitted=False,
                           interpretation='Last projection is a structural singleton, not a generated or useful fused backend')
    return graph


def require_complete_region(ir):
    graph=plan(ir)
    expected=[n['id'] for n in graph['nodes'][1:-1]]
    if len(graph['regions'])!=1 or graph['regions'][0]['nodes']!=expected:
        raise ValueError('Payment lowering incomplete; keep original execution')
    raise ValueError('Payment execution backend is not implemented')
