"""Name-independent, deliberately partial bytecode forwarding summaries.

Only a straight-line JVM subset is accepted. A call is an ordered effect, even
when its result is discarded. Values are provenance symbols, never copied data.
Cost closure needs explicitly supplied terminal contracts and original default
dispatch on the SAME repository proxy. This is not general virtual resolution,
alias analysis, SQL parsing, backend generation or an optimization algorithm.
"""
import copy


class Unsupported(ValueError):
    pass


def forwarding_values(method):
    try:
        if not method.get('owner_access',0) & 0x200 or method['access'] & 0x8:
            raise Unsupported('only instance interface bodies; class/static initialization not analyzed')
        if method['access'] & (0x100 | 0x400 | 0x20):
            raise Unsupported('native, abstract or synchronized method')
        if method['exception_handlers']:
            raise Unsupported('exception handler requires control-flow analysis')
        if not method['instructions']:
            raise Unsupported('missing body')
        locals_={p['slot']:dict(kind='parameter',position=p['position'],type=p['type']) for p in method['parameters']}
        if not method['access'] & 0x8:
            locals_[0]=dict(kind='receiver',type='L'+method['owner']+';')
        stack=[];calls=[];null_casts=[];returned=False;result=None
        for ins in method['instructions']:
            op=ins['opcode']
            if returned:raise Unsupported('instructions after return')
            if op==0:continue
            if op in range(21,26):
                if ins['slot'] not in locals_:raise Unsupported('load from unresolved local')
                stack.append(copy.deepcopy(locals_[ins['slot']]))
            elif op==1:stack.append(dict(kind='constant',type='null',value=None))
            elif 2<=op<=8:stack.append(dict(kind='constant',type='I',value=op-3))
            elif op in (9,10):stack.append(dict(kind='constant',type='J',value=op-9))
            elif op in (16,17):stack.append(dict(kind='constant',type='I',value=ins['operand']))
            elif op==18 and ins.get('constant_kind') in ('java.lang.String','java.lang.Integer','java.lang.Long'):
                kind={'java.lang.String':'Ljava/lang/String;','java.lang.Integer':'I','java.lang.Long':'J'}[ins['constant_kind']]
                stack.append(dict(kind='constant',type=kind,value=ins['constant']))
            elif op==192:
                # CHECKCAST preserves a statically proven null. A cast of any
                # parameter/result can fail and requires a richer summary.
                if not stack or stack[-1]!=dict(kind='constant',type='null',value=None):
                    raise Unsupported('cast of nonconstant-null requires type/failure analysis')
                null_casts.append(dict(index=ins['index'],target=ins['type_operand']))
            elif op in (182,183,184,185):
                if ins['name']=='<init>':raise Unsupported('constructor/uninitialized-object semantics')
                count=ins['argument_count']
                args=stack[-count:] if count else []
                if count:del stack[-count:]
                if len(args)!=count:raise Unsupported('argument stack underflow')
                receiver=None if op==184 else stack.pop()
                call=dict(index=ins['index'],target=ins['target'],owner=ins['owner'],opcode=op,
                          receiver=receiver,arguments=args,return_type=ins['return_type'])
                calls.append(call)
                if ins['return_type']!='V':stack.append(dict(kind='call_result',call=len(calls)-1,type=ins['return_type']))
            elif op==87:
                value=stack.pop()
                if value['type'] in ('J','D'):raise Unsupported('category-2 POP')
            elif 172<=op<=177:
                result=None if op==177 else stack.pop()
                if stack:raise Unsupported('unexpected residual return stack')
                returned=True
            else:raise Unsupported('unsupported opcode '+str(op)+' at '+str(ins['index']))
        if not returned:raise Unsupported('missing return')
        return dict(status='supported_forwarding_values',method=method['id'],calls=calls,return_value=result,null_casts=null_casts,
                    identity='parameter symbols denote original references; repeated symbols preserve aliasing; no serialization',
                    order='calls in bytecode order; exceptions propagate and stop the remaining prefix')
    except (Unsupported,IndexError) as error:
        return dict(status='unknown',method=method['id'],reason=str(error) or 'stack underflow')


def close_forwarding(method_id, methods, terminals, active=()):
    """Compose explicit terminal effects through same-receiver wrappers.

    Terminals carry declared cost/effects, not an assumption that external calls
    are pure. All other dispatches, recursion and unsupported bodies stay unknown.
    """
    if method_id in active:return dict(status='unknown',method=method_id,reason='recursive forwarding cycle')
    if method_id not in methods:return dict(status='unknown',method=method_id,reason='missing method body')
    method=methods[method_id];values=forwarding_values(method)
    if values['status']=='unknown':return values
    events=[];call_returns={}
    def resolve(value):
        if value is None:return None
        if value['kind']=='call_result':return copy.deepcopy(call_returns[value['call']])
        return copy.deepcopy(value)
    for call_index,call in enumerate(values['calls']):
        if (call['owner']!=method['owner'] or not call['receiver'] or call['receiver']['kind']!='receiver'
                or call['opcode'] not in (183,185)):
            return dict(status='unknown',method=method_id,reason='unproven same-proxy dispatch',call=call)
        args=[resolve(a) for a in call['arguments']]
        if call['target'] in terminals:
            terminal=terminals[call['target']]
            cost=terminal.get('java_calls',{})
            if type(cost.get('base')) is not int or cost['base']<0 or cost.get('terms'):
                return dict(status='unknown',method=method_id,reason='terminal needs a constant nonnegative call contract')
            if terminal.get('return_type')!=call['return_type']:
                return dict(status='unknown',method=method_id,reason='terminal return descriptor mismatch')
            event=len(events)
            events.append(dict(terminal=call['target'],arguments=args,contract=copy.deepcopy(terminal)))
            call_returns[call_index]=None if call['return_type']=='V' else dict(kind='terminal_result',event=event,type=call['return_type'])
        else:
            child=close_forwarding(call['target'],methods,terminals,active+(method_id,))
            if child['status']=='unknown':return dict(status='unknown',method=method_id,reason='unclosed callee',callee=child)
            offset=len(events)
            def substitute(value):
                if value is None:return None
                if value['kind']=='parameter':return copy.deepcopy(args[value['position']])
                if value['kind']=='terminal_result':return dict(value,event=value['event']+offset)
                if value['kind']=='receiver':return copy.deepcopy(call['receiver'])
                return copy.deepcopy(value)
            for event in child['events']:
                events.append(dict(event,arguments=[substitute(a) for a in event['arguments']]))
            call_returns[call_index]=substitute(child['return_value'])
    return dict(status='closed_forwarding',method=method_id,events=events,return_value=resolve(values['return_value']),
                direct_null_casts=values['null_casts'],
                java_calls=dict(base=sum(e['contract']['java_calls']['base'] for e in events),terms={}),
                dispatch_assumption='original interface default bodies and declared SQL terminals on the same NPJT proxy; no custom overrides',
                ordering='terminal calls stay in source order, including unused results; no exception swallowing or retries')


def forwarding_dag(method, summary, transaction):
    """Express recovered value/effect order in the existing placement schema.

    Each terminal remains atomic and host-only. Literal arguments are carried
    inline; object-valued parameters denote references, not serialized copies.
    """
    if summary['status']!='closed_forwarding':raise ValueError('Unclosed forwarding summary')
    external={f'arg_{p["position"]}':p['type'] for p in method['parameters']}
    external['receiver']='original_repository_proxy'
    def uses(values):
        result={}
        for value in values:
            if value is None:continue
            kind=value['kind']
            if kind=='parameter':result[f'arg_{value["position"]}']=value['type']
            elif kind=='terminal_result':result[f'result_{value["event"]}']=value['type']
            elif kind=='receiver':result['receiver']='original_repository_proxy'
            elif kind!='constant':raise ValueError('Unknown provenance symbol')
        return result
    nodes=[];edges=[]
    for i,event in enumerate(summary['events']):
        node=dict(id=f'call_{i}',transaction=transaction,body_known=True,
                  uses=dict(uses(event['arguments']),receiver='original_repository_proxy'),
                  defines={} if event['contract']['return_type']=='V' else {f'result_{i}':event['contract']['return_type']},
                  java_calls=copy.deepcopy(event['contract']['java_calls']),database=None,
                  queries=[event['contract']['sql']] if 'sql' in event['contract'] else [],
                  requires=['preserve_parameter_reference_identity','preserve_ordered_effect_and_failure_prefix'],
                  source=copy.deepcopy(event),retention_reason='Recovered forwarding is not database lowering or a wire codec')
        nodes.append(node)
    nodes.append(dict(id='return',transaction=transaction,body_known=True,uses=uses([summary['return_value']]),
                      defines={} if method['return_type']=='V' else {'method_result':method['return_type']},
                      java_calls=dict(base=0,terms={}),database=None,queries=[],requires=['preserve_original_return'],
                      source=dict(return_value=summary['return_value']),retention_reason='Original return contract retained'))
    definitions={}
    for i,node in enumerate(nodes):
        if i:edges.append(dict(before=nodes[i-1]['id'],after=node['id'],kind='original_effect_and_failure_order',resource='execution_prefix'))
        for value in node['uses']:
            if value in definitions:edges.append(dict(before=definitions[value],after=node['id'],kind='value',resource=value))
        definitions.update({v:node['id'] for v in node['defines']})
    return dict(nodes=nodes,edges=edges,external_inputs=external,parameters={},wire_types={},max_boundary_values=None,
                scope=dict(method=method['id'],authority='automatically recovered straight-line forwarding under explicit terminal/dispatch contracts',
                           limits='parameter identity and call-result provenance only; no heap alias inference, serialization or database lowering'))
