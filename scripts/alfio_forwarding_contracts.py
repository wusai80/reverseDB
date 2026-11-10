"""Bind the generic forwarding analysis to the pinned audit call site."""
import copy
from forwarding_contracts import forwarding_values, close_forwarding, forwarding_dag

OWNER='alfio/repository/AuditingRepository'
SHA='dcedd5eada22d1987ac9c3bfb6e2c6c63877c3c44efb8c6c2b0d6e870cff5a9f'


def attach_audit_forwarding(flows, facts, bytecode):
    source=next(c for c in facts['classes'] if c['class']==OWNER)
    extracted=next(c for c in bytecode['classes'] if c['class']==OWNER)
    if source['sha256']!=SHA or extracted['sha256']!=SHA:
        raise ValueError('Unsupported audit repository binary')
    result=copy.deepcopy(flows)
    node=next(n for n in result['reservation_entry']['nodes'] if n['id']=='audit_creation')
    calls=node['source']['calls']
    if [c['target'] for c in calls[:-1]]!=['alfio/model/Event#getId()I','java/lang/Integer#valueOf(I)Ljava/lang/Integer;','java/util/Date#<init>()V']:
        raise ValueError('Audit host argument effects changed')
    wrapper=calls[-1]['target']
    methods={m['id']:m for m in extracted['methods']}
    values=forwarding_values(methods[wrapper])
    if values['status']!='supported_forwarding_values' or len(values['calls'])!=1:
        raise ValueError('Audit wrapper no longer a single forward')
    leaf=values['calls'][0]['target']
    original=next(m for m in source['methods'] if m['id']==leaf)
    annotations=[a for a in original['annotations'] if a['type']=='Lch/digitalfondue/npjt/Query;']
    if len(annotations)!=1 or 'type' in annotations[0]['values'] or not original['access'] & 0x400:
        raise ValueError('Expected original abstract ordinary NPJT query')
    terminal=dict(java_calls=dict(base=1,terms={}),return_type='I',sql=annotations[0]['values']['value'],
                  authority='pinned abstract NPJT audit INSERT: one normal-path execute, not inferred from wrapper syntax',
                  parameters=copy.deepcopy(original['parameters']))
    summary=close_forwarding(wrapper,methods,{leaf:terminal})
    if summary['status']!='closed_forwarding':raise ValueError('Unclosed original audit wrapper')
    bindings=[]
    for p in original['parameters']:
        bind=next(a['values']['value'] for a in p['annotations'] if a['type']=='Lch/digitalfondue/npjt/Bind;')
        bindings.append(dict(sql_parameter=bind,source=summary['events'][0]['arguments'][p['position']]))
    summary['sql_bindings']=bindings
    summary['source_class_sha256']=SHA
    node['java_calls']=copy.deepcopy(summary['java_calls']);node.pop('cost_unknown_reason',None)
    node['cost_basis']='original Event getter, boxing and host Date creation plus source-closed audit forwarding; one execute on success'
    node['queries']=[terminal['sql']]
    node['source']['forwarding_summary']=summary
    node['requires'].append('preserve_host_clock_read_at_original_phase')
    result['audit_forwarding']=forwarding_dag(methods[wrapper],summary,node['transaction'])
    link=dict(parent='reservation_entry',node='audit_creation',child='audit_forwarding',
              kind='one source-closed default-method call; host argument creation stays in parent')
    return result,link,summary
