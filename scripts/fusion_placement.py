"""Name-independent ordered placement with explicit backend/wire contracts.

Exact DP for an additive call-count model and contiguous atomic units. Every DB
region marshals its live values through the host coordinator; no session cache,
reordering, semantic proof synthesis or backend generation is assumed.
"""
from fusion_regions import sql_effect, schedule, validate_schedule


def cost_value(cost, parameters):
    return cost.get('base', 0)+sum(k*parameters[v] for v,k in cost.get('terms', {}).items())


def check_contract(contract):
    nodes=contract['nodes']; names=[n['id'] for n in nodes]
    if len(set(names))!=len(names): raise ValueError('Duplicate node identity')
    domains=contract.get('parameters',{})
    for bounds in domains.values():
        if len(bounds)!=2 or not all(isinstance(x,int) for x in bounds) or bounds[0]<0 or bounds[1]<bounds[0]:
            raise ValueError('Invalid nonnegative parameter domain')
    available=dict(contract['external_inputs'])
    for n in nodes:
        cost=n['java_calls']
        if not isinstance(cost.get('base',0),int) or cost.get('base',0)<0: raise ValueError('Invalid Java cost')
        if any(v not in domains or not isinstance(k,int) or k<0 for v,k in cost.get('terms',{}).items()):
            raise ValueError('Invalid monotone affine cost')
        for v,t in n.get('uses',{}).items():
            if available.get(v)!=t: raise ValueError('Unresolved or mismatched value: '+v)
        for v,t in n.get('defines',{}).items():
            if v in available: raise ValueError('Non-SSA value: '+v)
            available[v]=t
    index={n:i for i,n in enumerate(names)}
    for edge in contract['edges']:
        if edge['before'] not in index or edge['after'] not in index or index[edge['before']]>=index[edge['after']]:
            raise ValueError('Dependency contradicts supplied source order')
    budget=contract.get('max_boundary_values')
    if budget is not None and (not isinstance(budget,int) or budget<0): raise ValueError('Invalid boundary budget')


def inspect_interval(contract, start, stop):
    """stop is exclusive; values defined/consumed wholly inside never cross ABI."""
    nodes=contract['nodes']; part=nodes[start:stop]; reasons=[]
    if not part: return dict(allowed=False,reasons=[dict(kind='empty_interval')])
    tx=part[0].get('transaction')
    if not tx or any(n.get('transaction')!=tx for n in part): reasons.append(dict(kind='transaction_boundary'))
    for n in part:
        if not n.get('body_known',False): reasons.append(dict(kind='unknown_body_effect',node=n['id']))
        capability=n.get('database')
        if not capability:
            reasons.append(dict(kind='no_database_lowering',node=n['id'],detail=n.get('retention_reason','unknown semantics')))
            continue
        if not capability.get('verified_source_contract') or not capability.get('lowering'):
            reasons.append(dict(kind='unverified_lowering',node=n['id']))
        missing=sorted(set(n.get('requires',[]))-set(capability.get('guarantees',[])))
        if missing: reasons.append(dict(kind='unmet_semantic_obligation',node=n['id'],obligations=missing))
        if capability.get('transaction_policy')!='same_outer_transaction':
            reasons.append(dict(kind='transaction_policy',node=n['id']))
        if any(not sql_effect(q)['known'] for q in n.get('queries',[])):
            reasons.append(dict(kind='unknown_sql_effect',node=n['id']))
    defined={v for n in part for v in n.get('defines',{})}
    live_in={v:t for n in part for v,t in n.get('uses',{}).items() if v not in defined}
    consumed_later={v for n in nodes[stop:] for v in n.get('uses',{})}
    live_out={v:t for n in part for v,t in n.get('defines',{}).items() if v in consumed_later}
    wires=contract['wire_types']
    for direction,values in [('host_to_database',live_in),('database_to_host',live_out)]:
        for v,t in values.items():
            codec=wires.get(t,{})
            if not codec.get(direction): reasons.append(dict(kind='missing_wire_codec',value=v,type=t,direction=direction))
    # Structural slot budget only. Never truncate values or pretend this is a
    # byte/row/memory estimate; repeated transfer at separate regions is counted.
    boundary_values=len(live_in)+len(live_out)
    if contract.get('max_boundary_values') is not None and boundary_values>contract['max_boundary_values']:
        reasons.append(dict(kind='boundary_value_budget',required=boundary_values,limit=contract['max_boundary_values']))
    lower={p:b[0] for p,b in contract.get('parameters',{}).items()}
    if sum(cost_value(n['java_calls'],lower) for n in part)<1:
        reasons.append(dict(kind='may_add_call_at_legal_minimum'))
    return dict(allowed=not reasons,reasons=reasons,nodes=[n['id'] for n in part],start=start,stop=stop,
                transaction=tx,live_in=live_in,live_out=live_out,boundary_values=boundary_values)


def plan_cost(contract,segments):
    result=dict(base=0,terms={})
    for segment in segments:
        if segment['placement']=='database': result['base']+=1
        else:
            for n in contract['nodes'][segment['start']:segment['stop']]:
                result['base']+=n['java_calls'].get('base',0)
                for p,k in n['java_calls'].get('terms',{}).items(): result['terms'][p]=result['terms'].get(p,0)+k
    return result


def validate_plan(contract, segments, phase_events=None):
    check_contract(contract); cursor=0; violations=[]
    for segment in segments:
        start,stop=segment.get('start'),segment.get('stop')
        if not isinstance(start,int) or not isinstance(stop,int) or start!=cursor or stop<=start or stop>len(contract['nodes']):
            violations.append(dict(kind='coverage_or_order',segment=segment)); continue
        cursor=stop
        if segment.get('placement')=='database':
            check=inspect_interval(contract,start,stop)
            if not check['allowed']: violations.extend(check['reasons'])
        elif segment.get('placement')!='java': violations.append(dict(kind='unknown_placement'))
    if cursor!=len(contract['nodes']): violations.append(dict(kind='missing_units'))
    events=phase_events if phase_events is not None else schedule([n['id'] for n in contract['nodes']])
    validation=validate_schedule(dict(nodes=contract['nodes'],edges=contract['edges']),events)
    violations.extend(validation['violations'])
    return dict(accepted=not violations,violations=violations)


def choose_placement(contract):
    check_contract(contract); nodes=contract['nodes']; count=len(nodes)
    upper={k:v[1] for k,v in contract.get('parameters',{}).items()}
    # Lexicographic objective: worst-case JDBC calls, then fewer moved units,
    # then fewer DB regions. Ties preserve the existing Java implementation.
    best=[None]*(count+1); best[0]=((0,0,0),[])
    candidates=[]; rejected=[]
    for stop in range(1,count+1):
        old_score,old_plan=best[stop-1]
        java=dict(placement='java',start=stop-1,stop=stop,nodes=[nodes[stop-1]['id']])
        best[stop]=((old_score[0]+cost_value(nodes[stop-1]['java_calls'],upper),old_score[1],old_score[2]),old_plan+[java])
        for start in range(stop):
            interval=inspect_interval(contract,start,stop)
            if not interval['allowed']:
                rejected.append(interval); continue
            candidates.append(interval)
            score,previous=best[start]
            candidate_score=(score[0]+1,score[1]+stop-start,score[2]+1)
            if candidate_score<best[stop][0]:
                segment=dict(interval,placement='database'); best[stop]=(candidate_score,previous+[segment])
    score,segments=best[count]
    validation=validate_plan(contract,segments)
    if not validation['accepted']: raise ValueError('Internal placement validation failed')
    original=[dict(placement='java',start=0,stop=count)] if count else []
    return dict(kind='ordered-placement-dp/v1',segments=segments,objective=list(score),
                original_calls=plan_cost(contract,original),selected_calls=plan_cost(contract,segments),
                parameter_domains=contract.get('parameters',{}),
                database_regions=[s for s in segments if s['placement']=='database'],
                legal_intervals=candidates,rejected_intervals=rejected,validation=validation,
                objective_definition='Minimize worst-case normal-path JDBC execute calls over the supplied nonnegative parameter box; ties prefer fewer moved units, then fewer regions',
                limits='Optimal only for supplied atomic units, verified-by-frontend lowering claims, coordinator ABI, interval choices and additive call model; no latency or equivalence proof')
