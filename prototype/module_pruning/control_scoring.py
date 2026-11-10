"""Common-cohort control-feature ablation, with explicit unknown abstention."""
from collections import Counter

from calibrate_risk import feature_key, fit, metrics, predict


def admitted(features, names):
    return isinstance(features, dict) and all(type(features.get(n)) is bool for n in names)


def diagnostic_predict(model, features, domain, required_names):
    if domain != model["domain"]:
        return dict(probability=None, status="abstain", reason="outside_calibration_domain", deletion_authorized=False)
    if not admitted(features, required_names):
        return dict(probability=None, status="abstain", reason="unknown_features", deletion_authorized=False)
    return dict(predict(model, features, domain), deletion_authorized=False)


def prepare(records, protocol):
    names = protocol["old_features"] + protocol["control_features"]
    groups = {}
    for group, ids in protocol["groups"].items():
        for ident in ids:
            if ident in groups:
                raise ValueError("Duplicate grouped id: " + ident)
            groups[ident] = group
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)) or set(ids) != set(groups):
        raise ValueError("Corpus/group membership mismatch")
    included, excluded = [], []
    for record in records:
        features = record.get("features")
        if not admitted(features, names):
            excluded.append(dict(id=record["id"], reason="unknown_features",
                                 missing=[n for n in names if not isinstance(features, dict) or type(features.get(n)) is not bool],
                                 probability=None, status="abstain", deletion_authorized=False))
            continue
        if type(record.get("label")) is not int or record["label"] not in (0,1):
            raise ValueError("Eligible development label must be resolved")
        included.append(dict(id=record["id"], family=groups[record["id"]], domain=protocol["domain"],
                             label=record["label"], features={n: features[n] for n in names}))
    return included, excluded


def classification(rows, cutoff):
    unsafe = [r for r in rows if r["label"] == 1]
    safe = [r for r in rows if r["label"] == 0]
    proposals = [r for r in rows if r["probability"] < cutoff]
    return dict(necessary_denominator=len(unsafe), removable_denominator=len(safe),
                nominations=len(proposals),
                false_removal_ids=[r["id"] for r in unsafe if r["probability"] < cutoff],
                false_retention_ids=[r["id"] for r in safe if r["probability"] >= cutoff])


def collisions(rows, names):
    cells = {}
    for row in rows:
        cells.setdefault(feature_key(row["features"], names), []).append(row)
    mixed = []
    for key, members in cells.items():
        counts = Counter(r["label"] for r in members)
        if len(counts) > 1:
            mixed.append(dict(key=key, ids=[r["id"] for r in members],
                              necessary=counts[1], removable=counts[0]))
    return dict(mixed_cells=mixed, minimum_empirical_classification_errors=sum(min(c["necessary"], c["removable"]) for c in mixed),
                denominator=len(rows), interpretation="Descriptive feature collision count on seen data; not held-out classification performance")


def evaluate(records, protocol):
    rows, excluded = prepare(records, protocol)
    groups = sorted({r["family"] for r in rows})
    if len(groups) < 3:
        raise ValueError("At least three complete mechanism groups required")
    all_names = protocol["old_features"] + protocol["control_features"]
    arms = {"old_eight": protocol["old_features"], "with_control": all_names}
    forecasts = {name: [] for name in [*arms, "constant_half", "training_prior"]}
    folds = []
    for group in groups:
        train = [r for r in rows if r["family"] != group]
        test = [r for r in rows if r["family"] == group]
        fold = dict(held_out_group=group, train_ids=[r["id"] for r in train], test_ids=[r["id"] for r in test], models={})
        for name, feature_names in arms.items():
            model = fit(train, {**protocol, "features": feature_names})
            if group in model["training_families"] or set(model["training_ids"]) & set(fold["test_ids"]):
                raise ValueError("Held-out group leakage")
            fold["models"][name] = model
            for row in test:
                prediction = diagnostic_predict(model, row["features"], row["domain"], all_names)
                forecasts[name].append(dict(id=row["id"], family=group, label=row["label"], **prediction))
            if name == "old_eight":
                for row in test:
                    for baseline, probability in [("constant_half",.5),("training_prior",model["prior"])]:
                        forecasts[baseline].append(dict(id=row["id"], family=group, label=row["label"], probability=probability))
        if fold["models"]["old_eight"]["prior"] != fold["models"]["with_control"]["prior"]:
            raise ValueError("Ablation training cohort mismatch")
        folds.append(fold)
    summaries = {name: metrics(preds, protocol["reliability_bin_edges"]) for name,preds in forecasts.items()}
    return dict(eligible_ids=[r["id"] for r in rows], excluded=excluded,
                coverage=dict(total=len(records), eligible=len(rows), fraction=len(rows)/len(records),
                              eligible_necessary=sum(r["label"] for r in rows), eligible_removable=sum(1-r["label"] for r in rows)),
                active_mechanism_groups=groups, independent_source_groups=protocol["source_groups"],
                folds=folds, forecasts=forecasts, metrics=summaries,
                unseen_cells={name: sum(p["source"] == "training_prevalence" for p in forecasts[name]) for name in arms},
                diagnostic_classification={name: classification(preds,protocol["classification_cutoff"]) for name,preds in forecasts.items()},
                representation={name: collisions(rows,names) for name,names in arms.items()},
                control_beats_old_on_both_metrics=all(summaries["with_control"][m] < summaries["old_eight"][m] for m in ["macro_brier","macro_log_loss"]),
                operational_abstentions=len(records), authorized_deletions=0, production_authorized=False)
