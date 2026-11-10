"""Check small attributed binding slices under explicit caller/runtime premises.

No call-graph matching, SQL destination proof or unrestricted Java equivalence.
Equal terms certify only the selected boundary value and setter overload.
"""

TIMESTAMP_SETTER='java.sql.PreparedStatement#setTimestamp(int,java.sql.Timestamp)'


def unknown_term(term):
    if isinstance(term,dict):
        return term.get('op')=='unknown' or any(unknown_term(v) for v in term.values())
    if isinstance(term,list): return any(unknown_term(v) for v in term)
    return False


def inspect_binding(facts,owner,slot):
    calls=[b for b in facts['bindings'] if b['owner']==owner]
    hits=[b for b in calls if b['operation'].startswith('set') and b['arguments'] and b['arguments'][0]=={'op':'literal','value':slot}]
    if len(hits)!=1: return dict(status='unknown',reason='missing_or_multiple_bindings',count=len(hits))
    b=hits[0]; same=[c for c in calls if c['receiver']==b['receiver']]
    if b['receiver']=='unknown' or not b['receiver_stable'] or b['guards']:
        return dict(status='unknown',reason='receiver_or_control_not_closed',binding=b)
    if any(c['operation'] in ('clearParameters','addBatch','clearBatch') or
           (c['operation'].startswith('set') and (not c['arguments'] or c['arguments'][0].get('op')!='literal')) for c in same):
        return dict(status='unknown',reason='dynamic_slot_or_binding_lifecycle',binding=b)
    executes=[c for c in same if c['operation'].startswith('execute')]
    if len(executes)!=1 or executes[0]['offset']<b['offset'] or executes[0]['guards']:
        return dict(status='unknown',reason='execution_not_single_unconditional_after_binding',binding=b)
    if len(b['arguments'])!=2 or unknown_term(b['arguments'][1]):
        return dict(status='unknown',reason='value_or_overload_not_supported',binding=b)
    return dict(status='extracted',setter=b['target'],value=b['arguments'][1],binding=b)


def compare(reference,candidate,command_parameter=1):
    """Frozen runtime premise: original setTimestamp is replaced by Timestamp(clock).

    Caller mapping and SQL sink correspondence are supplied, not discovered here.
    Never consumes verified_source_contract/guarantees from the placement catalog.
    """
    if reference['status']!='extracted' or candidate['status']!='extracted':
        return dict(status='unknown',reason='incomplete_slice')
    if reference['setter']!=TIMESTAMP_SETTER or reference['value']!={'op':'timestamp','value':{'op':'wall_clock'}}:
        return dict(status='unknown',reason='reference_outside_frozen_clock_rule')
    expected={'op':'timestamp','value':{'op':'json_long','receiver':{'op':'parameter','position':command_parameter,'type':'org.json.JSONObject'},
      'key':{'op':'literal','value':'clock_ms'}}}
    equal=candidate['setter']==reference['setter'] and candidate['value']==expected
    return dict(status='matched_under_premises' if equal else 'different_structure',
      expected_setter=reference['setter'],expected_value=expected,candidate_setter=candidate['setter'],candidate_value=candidate['value'],
      scope='Selected binding expression and JDBC overload only; not whole-transaction equivalence',
      premises=['frozen JdbcTrace clock replacement at mapped original SQL slot',
        'same validated JSONObject command and pinned pgJDBC driver',
        'caller-supplied original/candidate SQL slot correspondence',
        'no unmodeled alias/heap mutation of command, Timestamp or statement before execution'])
