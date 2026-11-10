#!/usr/bin/env python3
"""Describe frozen holdout results without modifying the model or experiment."""
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = Path(sys.argv[1]).resolve()
    manifest = json.loads((source/"manifest.json").read_text())
    for name, value in manifest.items():
        if digest(source/name) != value:
            raise ValueError(f"Changed archive input: {name}")
    run = json.loads((source/"run.json").read_text())
    calibration = json.loads((source/"calibration.json").read_text())
    evaluation = json.loads((source/"evaluation.json").read_text())
    model = json.loads((source/"model.json").read_text())
    if run["status"] != "passed" or run["cleanup_errors"] or calibration["timed_samples"] != 120 or evaluation["timed_samples"] != 300:
        raise ValueError("Incomplete fixed experiment")
    if digest(source/"model.json") != run["model_sha256_before_evaluation"] or model["calibration_sha256"] != digest(source/"calibration.json"):
        raise ValueError("Model provenance mismatch")
    if {c["seed"] for c in calibration["cases"]} != {42} or {c["seed"] for c in evaluation["cases"]} != {43,44}:
        raise ValueError("Calibration/evaluation boundary changed")
    by_cell = defaultdict(list)
    decisions = []
    correct = 0
    for case in evaluation["cases"]:
        if not case["state_unchanged"] or len(case["samples"]) != 25:
            raise ValueError("Bad evaluation case")
        medians = {arm: statistics.median(s["elapsed_ns"]/1e6 for s in case["samples"] if s["arm"] == arm)
                   for arm in evaluation["protocol"]["evaluation_arms"]}
        best = min(evaluation["protocol"]["direct_arms"], key=medians.get)
        selected = Counter(s["route"] for s in case["samples"] if s["arm"] == "selector")
        correct += selected[best]
        decisions.append(dict(seed=case["seed"], page_size=case["page_size"], overlap_percent=case["overlap_percent"],
                              measured_median_best=best, selected=dict(selected), median_ms=medians,
                              selected_plan_to_best_ratio={arm:medians[arm]/medians[best] for arm in selected},
                              selector_full_to_best_ratio=medians["selector"]/medians[best]))
        for sample in case["samples"]:
            if not sample["output_verified"] or not sample["cache_released"] or sample["cache_owned_bytes"] > case["cache_budget_bytes"]:
                raise ValueError("Output/lifetime/budget failure")
        by_cell[(case["page_size"],case["overlap_percent"])].append(case)
    cells = []
    for (page,overlap), cases in sorted(by_cell.items()):
        measurements = defaultdict(list)
        selections = []
        routes = Counter()
        for case in cases:
            for sample in case["samples"]:
                measurements[sample["arm"]].append(sample["elapsed_ns"]/1e6)
                if sample["arm"] == "selector":
                    selections.append(sample["selection_ns"]/1e6)
                    routes[sample["route"]] += 1
        medians = {arm:statistics.median(values) for arm,values in measurements.items()}
        independent = min(("independent_correlated","independent_grouped"),key=medians.get)
        best = min(evaluation["protocol"]["direct_arms"],key=medians.get)
        cells.append(dict(page_size=page,overlap_percent=overlap,samples_per_arm=10,median_ms=medians,
                          selection_median_ms=statistics.median(selections),selected_routes=dict(routes),
                          best_direct=best,best_independent=independent,
                          db_shared_to_independent_ratio=medians["db_shared"]/medians[independent],
                          app_shared_to_best_ratio=medians["app_shared"]/medians[best],
                          selector_to_best_ratio=medians["selector"]/medians[best],
                          required_cache_bytes=cases[0]["required_cache_bytes"]))
    result = dict(status="complete",source_archive=str(source.relative_to(ROOT)),
                  source_manifest_sha256=digest(source/"manifest.json"),analyzer_sha256=digest(Path(__file__)),
                  semantic_checks=17,calibration_samples=120,evaluation_samples=300,cells=cells,decisions=decisions,
                  decision_reference="Best of four direct-plan medians on the same holdout case; descriptive, not true-optimal oracle",
                  selection_matches=dict(matches=correct,total=60),
                  model_rmse_ms=model["calibration_rmse_ms"],model_weights=model["weights"],
                  limitations=["one local database; separate JVMs for calibration and evaluation",
                               "new data seeds but same declared workload shapes",
                               "small constrained linear model, manually defined SQL templates",
                               "memory budget covers packed cache and cache control object only",
                               "online EXPLAIN costs included; offline calibration reported separately",
                               "no result-driven refit or new sample matrix"])
    target = ROOT/"evidence/app-db-reuse-analysis"/source.name
    target.mkdir(parents=True,exist_ok=False)
    (target/"analysis.json").write_text(json.dumps(result,indent=2)+"\n")
    (target/"analyze_workflow_reuse.py").write_bytes(Path(__file__).read_bytes())
    (target/"manifest.json").write_text(json.dumps({p.name:digest(p) for p in sorted(target.iterdir())},indent=2)+"\n")
    print("page overlap independent_corr independent_group db_shared app_shared selector selection_only")
    for cell in cells:
        print(cell["page_size"],cell["overlap_percent"],*(f"{cell['median_ms'][arm]:.3f}" for arm in evaluation["protocol"]["evaluation_arms"]),f"{cell['selection_median_ms']:.3f}")
    print(json.dumps(dict(output=str(target),selection_matches=result["selection_matches"])))


if __name__=="__main__":
    main()
