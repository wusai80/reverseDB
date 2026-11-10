"""Small executable finite programs, not simulations of real database internals.

Labels come from exhaustive observations, not static-feature heuristics.
The discarded-result feature and limited probe deliberately miss effects.
"""
import itertools


class Rejected(Exception):
    pass


def states(case, protocol):
    values=protocol["input_values"]
    # An explicit contract difference, retained in the same source family.
    if case["family"]=="exception_control" and case["variant"]==1:
        values=[0]
    return [dict(value=x,flag=flag) for x,flag in itertools.product(values,protocol["input_flags"])]


def execute(case, state, removed):
    x,flag=state["value"],state["flag"]
    v,family=case["variant"],case["family"]
    try:
        if family=="returned_value":
            slot=0
            if not removed:
                slot=0 if v==1 else x
            # v2 has no consumer; v3 has a conditional consumer.
            value=slot if v in (0,1) or (v==3 and flag) else 0
            return dict(status="ok",value=value)
        if family=="persistent_state":
            balance,scratch=0,0
            if not removed:
                if v==1: scratch+=x
                elif v==2: balance+=0
                elif v==3: balance+=x if flag else 0
                else: balance+=x
            return dict(status="ok",balance=balance)
        if family=="exception_control":
            if not removed and v!=2 and x!=0 and (v!=3 or flag):
                raise Rejected("MODULE_ERROR")
            return dict(status="ok",value=x)
        if family=="heap_alias":
            cell={"value":0}
            view=dict(cell) if v==1 else cell
            if not removed:
                cell["value"]+=0 if v==2 else x if v!=3 or flag else 0
            return dict(status="ok",value=view["value"])
        if family=="authorization_guard":
            denied=(not flag and x!=0) if v!=3 else x<0
            if not removed and v!=2 and denied:
                raise Rejected("DENIED")
            if v==1 and denied:  # independently retained caller guard
                raise Rejected("DENIED")
            return dict(status="ok",value=x)
        if family=="resource_lifetime":
            handle={"open":x!=0}
            observable={"open":False} if v==2 else handle
            if not removed and (v!=3 or flag):
                handle["open"]=False
            if v==1:  # independently retained final cleanup
                handle["open"]=False
            return dict(status="ok",handle_open=observable["open"])
        raise ValueError("Unknown program family")
    except Rejected as error:
        return dict(status="error",code=str(error))


def build_suite(protocol):
    rows=[]
    for family in protocol["families"]:
        for variant in range(protocol["variants_per_family"]):
            case=dict(id=f"{family}/{variant}",family=family,variant=variant,domain=protocol["domain"])
            allowed=states(case,protocol)
            probe=protocol["limited_probe"]
            if probe not in allowed: raise ValueError("Probe outside contract")
            original=execute(case,probe,False); deleted=execute(case,probe,True)
            features=dict(discarded_result=family!="returned_value" or variant==2,
                          limited_probe_pass=original==deleted)
            comparisons=[]
            for state in allowed:
                before,after=execute(case,state,False),execute(case,state,True)
                comparisons.append(dict(input=state,original=before,deleted=after,equal=before==after))
            witness=next((r for r in comparisons if not r["equal"]),None)
            rows.append({**case,"features":features,"label":int(witness is not None),
                         "label_origin":"exhaustive finite contractual observations", "allowed_states":allowed,
                         "limited_probe":dict(input=probe,original=original,deleted=deleted),
                         "exhaustive_comparisons":comparisons,"counterexample":witness})
    return rows
