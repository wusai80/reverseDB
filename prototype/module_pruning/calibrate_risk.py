"""Grouped, domain-limited calibration. Never treats an unknown label as safe."""
from collections import Counter
import math


def feature_key(features,names):
    if any(type(features.get(name)) is not bool for name in names):
        raise ValueError("Explicit Boolean evidence features required")
    return "".join("1" if features[name] else "0" for name in names)


def fit(rows,protocol):
    if not rows: raise ValueError("Empty training set")
    ids=[r["id"] for r in rows]
    if len(set(ids))!=len(ids): raise ValueError("Duplicate candidate IDs")
    if any(type(r.get("label")) is not int or r["label"] not in (0,1) for r in rows):
        raise ValueError("Unresolved labels cannot enter training")
    if any(r["domain"]!=protocol["domain"] for r in rows): raise ValueError("Training domain mismatch")
    counts=Counter(r["family"] for r in rows)
    if len(counts)<2 or {r["label"] for r in rows}!={0,1}:
        raise ValueError("Need multiple source families and both labels")
    alpha,beta=protocol["alpha"],protocol["beta"]
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>0 for x in (alpha,beta)):
        raise ValueError("Invalid smoothing")
    cells={}; positive=total=0.0
    for row in rows:
        weight=1/counts[row["family"]]
        key=feature_key(row["features"],protocol["features"])
        cell=cells.setdefault(key,dict(weight=0.0,positive_weight=0.0,case_ids=[]))
        cell["weight"]+=weight; cell["positive_weight"]+=weight*row["label"]; cell["case_ids"].append(row["id"])
        total+=weight; positive+=weight*row["label"]
    for cell in cells.values(): cell["probability"]=(cell["positive_weight"]+alpha)/(cell["weight"]+alpha+beta)
    return dict(domain=protocol["domain"],features=protocol["features"],training_families=sorted(counts),
                training_ids=ids,cells=cells,prior=(positive+alpha)/(total+alpha+beta),
                constant_features=[n for n in protocol["features"] if len({r["features"][n] for r in rows})==1],
                status="controlled_fixture_only",production_authorized=False,
                interpretation="smoothed family-weighted frequencies, not a credible interval or learned real-world base rate")


def predict(model,features,domain):
    if domain!=model["domain"]:
        return dict(probability=None,status="abstain",reason="outside_calibration_domain")
    key=feature_key(features,model["features"])
    cell=model["cells"].get(key)
    return dict(probability=model["prior"] if cell is None else cell["probability"],
                status="controlled_fixture_prediction",source="training_prevalence" if cell is None else "joint_evidence_cell")


def metrics(rows,bins):
    if not rows: raise ValueError("No evaluation rows")
    families={r["family"] for r in rows}
    per_family={}
    for family in sorted(families):
        group=[r for r in rows if r["family"]==family]
        per_family[family]=dict(brier=sum((r["probability"]-r["label"])**2 for r in group)/len(group),
            log_loss=-sum(math.log(r["probability"] if r["label"] else 1-r["probability"]) for r in group)/len(group))
    reliability=[]
    for i,(low,high) in enumerate(zip(bins,bins[1:])):
        group=[r for r in rows if low<=r["probability"] and (r["probability"]<high or (i==len(bins)-2 and r["probability"]==high))]
        reliability.append(dict(low=low,high=high,count=len(group),families=sorted({r["family"] for r in group}),
             predicted_mean=sum(r["probability"] for r in group)/len(group) if group else None,
             observed_fraction=sum(r["label"] for r in group)/len(group) if group else None))
    return dict(cases=len(rows),families=len(families),macro_brier=sum(v["brier"] for v in per_family.values())/len(families),
                macro_log_loss=sum(v["log_loss"] for v in per_family.values())/len(families),per_family=per_family,reliability=reliability)


def grouped_evaluation(rows,protocol,v0_predict):
    forecasts={name:[] for name in ["frozen_v0_soft_score","training_prevalence","joint_calibrator"]}
    folds=[]
    for family in sorted({r["family"] for r in rows}):
        train=[r for r in rows if r["family"]!=family]; test=[r for r in rows if r["family"]==family]
        model=fit(train,protocol)
        if family in model["training_families"]: raise ValueError("Family leakage")
        folds.append(dict(held_out_family=family,model=model,test_ids=[r["id"] for r in test]))
        for row in test:
            # Labels are used by fit for training families only; predictor takes features only.
            predictions={"joint_calibrator":predict(model,row["features"],row["domain"])["probability"],
                         "training_prevalence":model["prior"],"frozen_v0_soft_score":v0_predict(row["features"])}
            for name,q in predictions.items():
                forecasts[name].append(dict(id=row["id"],family=family,label=row["label"],probability=q))
    summary={name:metrics(preds,protocol["reliability_bin_edges"]) for name,preds in forecasts.items()}
    calibrated=summary["joint_calibrator"]
    better=all(calibrated[metric]<summary[baseline][metric] for metric in ["macro_brier","macro_log_loss"] for baseline in ["frozen_v0_soft_score","training_prevalence"])
    return dict(folds=folds,forecasts=forecasts,metrics=summary,beats_both_baselines=better,production_authorized=False)
