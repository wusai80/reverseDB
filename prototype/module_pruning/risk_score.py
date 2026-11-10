"""Uncalibrated Bayesian sensitivity analysis for module necessity.

No code transformation or execution. Evidence scope and grouping are inputs,
not inferred truths. A probability estimate never authorizes deletion.
"""
import itertools
import math


def number(value, name, low=0.0, high=None, strict=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite number required")
    if value < low or (strict and value == low) or (high is not None and value > high):
        raise ValueError(f"{name}: out of range")
    return float(value)


def grid(values, name, probability=False):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError(f"{name}: [low, central, high] required")
    result=[number(v,name,high=1.0 if probability else None,strict=True) for v in values]
    if result!=sorted(result) or (probability and result[-1]>=1):
        raise ValueError(f"{name}: ordered grid required; priors must be strictly between 0 and 1")
    return result


def posterior(prior, bayes_factors):
    log_odds=math.log(prior)-math.log1p(-prior)
    for factor in bayes_factors:
        log_odds+=math.log(number(factor,"Bayes factor",strict=True))
    if log_odds>=0:
        return 1/(1+math.exp(-log_odds))
    exp=math.exp(log_odds)
    return exp/(1+exp)


def entropy(q):
    return 0.0 if q in (0,1) else -q*math.log2(q)-(1-q)*math.log2(1-q)


def information_gain(q, sensitivity, false_positive):
    q=number(q,"q",high=1); s=number(sensitivity,"sensitivity",high=1); f=number(false_positive,"false_positive",high=1)
    positive=q*s+(1-q)*f
    negative=1-positive
    after=0.0
    if positive>0: after+=positive*entropy(q*s/positive)
    if negative>0: after+=negative*entropy(q*(1-s)/negative)
    return max(0.0,entropy(q)-after)


def validate_model(model):
    prior=grid(model["prior_grid"],"prior",True)
    groups={key:grid(value["bayes_factor_grid"],key) for key,value in model["groups"].items()}
    number(model["utility"]["loss_if_wrong"],"loss")
    number(model["utility"]["risk_aversion"],"risk aversion")
    number(model["validation_budget"],"budget")
    ids=set()
    for probe in model["probes"]:
        if probe["id"] in ids: raise ValueError("Duplicate probe ID")
        ids.add(probe["id"])
        number(probe["cost"],"probe cost",strict=True)
        values=probe["sensitivity_grid"]
        if len(values)!=3 or values!=sorted(values): raise ValueError("Invalid sensitivity grid")
        for value in values: number(value,"sensitivity",high=1)
        number(probe["false_positive"],"false positive",high=1)
    return prior,groups


def score_candidate(candidate, evidence, model):
    prior,group_grids=validate_model(model)
    ignored=[]; relevant=[]; ids={}
    for event in evidence:
        # One evidence ID cannot silently acquire a different verdict or scope.
        if event["id"] in ids and ids[event["id"]]!=event:
            raise ValueError("Conflicting evidence ID: "+event["id"])
        ids[event["id"]]=event
    for event in ids.values():
        if event["module"]!=candidate["module"] or event["contract"]!=candidate["contract"]:
            ignored.append(dict(id=event["id"],reason="different_module_or_contract"))
        else:
            if event["kind"] not in {"soft","counterexample"}: raise ValueError("Unknown evidence kind")
            relevant.append(event)
    factors={}
    for event in relevant:
        if event["kind"]=="soft":
            group=event["group"]
            if group not in group_grids: raise ValueError("Unmodeled evidence group: "+group)
            factors.setdefault(group,[]).append(event["id"])
    required=candidate.get("required_witnesses",[])
    counterexamples=[e["id"] for e in relevant if e["kind"]=="counterexample"]
    blocked=bool(required or counterexamples)
    reason="required_dependency" if required else "in_contract_counterexample" if counterexamples else None
    ordered=sorted(factors)
    scenarios=[1.0] if blocked else [posterior(p,fs) for p in prior for fs in itertools.product(*(group_grids[g] for g in ordered))]
    central=1.0 if blocked else posterior(prior[1],[group_grids[g][1] for g in ordered])
    gain=candidate.get("gain_grid")
    if gain is not None:
        if len(gain)!=3 or gain!=sorted(gain): raise ValueError("Invalid gain grid")
        gain=[number(g,"gain",low=-math.inf) for g in gain]
    penalty=model["utility"]["risk_aversion"]*model["utility"]["loss_if_wrong"]
    def utility(q,g): return (1-q)*g-penalty*q
    utility_result=None
    if gain is not None:
        values=[utility(q,g) for q in scenarios for g in gain]
        utility_result=dict(central=utility(central,gain[1]),sensitivity_envelope=[min(values),max(values)],unit=model["utility"]["unit"])
    probes=[]
    if not blocked and candidate.get("validation_eligible",True):
        for probe in model["probes"]:
            if probe["cost"]>model["validation_budget"]: continue
            values=[information_gain(q,s,probe["false_positive"])/probe["cost"] for q in scenarios for s in probe["sensitivity_grid"]]
            probes.append(dict(id=probe["id"],cost=probe["cost"],central_bits_per_cost=information_gain(central,probe["sensitivity_grid"][1],probe["false_positive"])/probe["cost"],sensitivity_envelope=[min(values),max(values)],status=probe["status"]))
        probes.sort(key=lambda p:(-p["sensitivity_envelope"][0],-p["central_bits_per_cost"],p["id"]))
    return dict(module=candidate["module"],contract=candidate["contract"],
                necessity_probability=dict(central=central,sensitivity_envelope=[min(scenarios),max(scenarios)],calibration=model["calibration"],scenario_count=len(scenarios)),
                hard_block=blocked,block_reason=reason,required_witnesses=required,counterexamples=counterexamples,
                counted_groups=[dict(group=g,evidence_ids=sorted(factors[g]),bayes_factor_grid=group_grids[g],update_count=1) for g in ordered],
                ignored_evidence=ignored,utility=utility_result,gain_grid=gain,
                validation_priority=probes,next_validation=probes[0]["id"] if probes and probes[0]["sensitivity_envelope"][0]>0 else None,
                ranking_eligible=not blocked and gain is not None,
                transformation_status=candidate.get("transformation_status","unknown"),
                deletion_authorized=False)


def rank_candidates(results):
    """Only compare known utilities under the SAME contract. No implicit zeros."""
    by_contract={}
    for result in results:
        if result["ranking_eligible"]:
            by_contract.setdefault(result["contract"],[]).append(result)
    return {contract:[r["module"] for r in sorted(rows,key=lambda r:(-r["utility"]["sensitivity_envelope"][0],-r["utility"]["central"],r["module"]))] for contract,rows in by_contract.items()}
