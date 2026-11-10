"""Frozen-model challenge evaluation. Diagnostics never bypass domain admission."""
import json

from calibrate_risk import feature_key, metrics, predict
from effect_features import extract


def observe(execute, state, removed):
    try:
        payload = execute(state["x"], state["flag"], removed)
    except Exception as error:
        return dict(kind="exception", type=type(error).__name__, message=str(error))
    # Snapshot the contractual value immediately; do not compare live aliases.
    return dict(kind="return", value=json.loads(json.dumps(payload, sort_keys=True)))


def forecast(source, discarded_result, probe_pass, domains, model, domain):
    if type(discarded_result) is not bool or type(probe_pass) is not bool:
        raise ValueError("Explicit source metadata and limited probe required")
    operational = predict(model, {}, domain)
    if operational["status"] != "abstain":
        raise ValueError("Challenge must retain its out-of-domain identity")
    result = dict(operational=operational, deletion_authorized=False)
    try:
        facts = extract(source, {"family": "external", "v": 0}, domains)
    except (ValueError, StopIteration) as error:
        return dict(result, extraction="unsupported", reason=str(error), diagnostic=None)
    features = dict(discarded_result=discarded_result, limited_probe_pass=probe_pass,
                    **facts["features"])
    key = feature_key(features, model["features"])
    cell = model["cells"].get(key)
    # Do not call predict(..., training_domain). This is explicitly an OOD diagnostic.
    diagnostic = dict(probability=model["prior"] if cell is None else cell["probability"],
                      source="training_prevalence" if cell is None else "joint_evidence_cell",
                      feature_key=key, calibrated_for_challenge=False)
    return dict(result, extraction="accepted_by_frozen_extractor", facts=facts,
                features=features, diagnostic=diagnostic)


def summarize(rows, cutoff):
    scored = [r for r in rows if r["prediction"]["diagnostic"] is not None]
    forecasts = [dict(id=r["id"], family=r["source_group"], label=r["label"],
                      probability=r["prediction"]["diagnostic"]["probability"]) for r in scored]
    def proposed(r): return r["prediction"]["diagnostic"]["probability"] < cutoff
    unsafe = [r for r in scored if r["label"] == 1]
    safe = [r for r in scored if r["label"] == 0]
    nominated = [r for r in scored if proposed(r)]
    false_removal = [r["id"] for r in unsafe if proposed(r)]
    false_retention = [r["id"] for r in safe if not proposed(r)]
    return dict(total_programs=len(rows), scorable=len(scored), unsupported=len(rows)-len(scored),
        source_groups=sorted({r["source_group"] for r in rows}),
        operational_abstentions=sum(r["prediction"]["operational"]["status"] == "abstain" for r in rows),
        authorized_deletions=sum(r["prediction"]["deletion_authorized"] for r in rows),
        finite_labels=dict(necessary=sum(r["label"] for r in rows), removable=sum(1-r["label"] for r in rows)),
        diagnostic_classification=dict(cutoff=cutoff, necessary_denominator=len(unsafe),
            removable_denominator=len(safe), nominations=len(nominated),
            false_removal_ids=false_removal, false_retention_ids=false_retention,
            false_removal_rate=len(false_removal)/len(unsafe) if unsafe else None,
            false_retention_rate=len(false_retention)/len(safe) if safe else None,
            unsafe_nomination_fraction=len(false_removal)/len(nominated) if nominated else None),
        diagnostic_metrics=metrics(forecasts, [0,.2,.4,.6,.8,1]) if forecasts else None,
        baseline_metrics=metrics([dict(r, probability=.5) for r in forecasts], [0,.2,.4,.6,.8,1]) if forecasts else None,
        unseen_cells=sum(r["prediction"]["diagnostic"]["source"] == "training_prevalence" for r in scored))
