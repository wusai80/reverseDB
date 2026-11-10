#!/usr/bin/env python3
"""Descriptive analysis only; no tuning, new samples, or significance claims."""
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_nodes(plan, path="root"):
    nodes = []
    if "Scan" in plan["Node Type"] or "Aggregate" in plan["Node Type"]:
        loops = plan.get("Actual Loops", 0)
        nodes.append(dict(path=path, node_type=plan["Node Type"], relation=plan.get("Relation Name"),
                          rows_per_loop=plan.get("Actual Rows", 0), loops=loops,
                          rows_times_loops=plan.get("Actual Rows", 0)*loops,
                          rows_removed_by_filter_times_loops=plan.get("Rows Removed by Filter", 0)*loops))
    for i, child in enumerate(plan.get("Plans", [])):
        nodes.extend(plan_nodes(child, f"{path}/{i}"))
    return nodes


def main():
    source = Path(sys.argv[1]).resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    for name, expected in manifest.items():
        if digest(source / name) != expected:
            raise ValueError(f"Archive mismatch: {name}")
    raw = json.loads((source / "raw.json").read_text())
    run = json.loads((source / "run.json").read_text())
    if raw["status"] != "passed" or run["status"] != "passed" or run["cleanup_errors"]:
        raise ValueError("Run not complete/clean")
    if len(raw["cases"]) != 24 or raw["timed_samples"] != 720 or raw["diagnostic_calls"] != 144:
        raise ValueError("Unexpected fixed matrix")
    cells = defaultdict(list)
    scans = []
    zero_memory_increments = 0
    for case in raw["cases"]:
        if not case["state_unchanged"] or len(case["samples"]) != 30:
            raise ValueError("Invalid case")
        key = (case["targets"], case["density"], case["page_percent"])
        cells[key].append(case)
        for diagnostic in case["diagnostics"]:
            zero_memory_increments += diagnostic["memory"]["peak_sum_minus_before_bytes"] == 0
            if diagnostic["client_to_server_bytes"] <= 0 or diagnostic["server_to_client_bytes"] <= 0:
                raise ValueError("Missing network measurement")
            scans.append(dict(targets=key[0], density=key[1], page_percent=key[2], seed=case["seed"],
                              arm=diagnostic["arm"], statements=[
                                  dict(nodes=plan_nodes(s["explain"]["Plan"]),
                                       planning_time_ms=s["explain"].get("Planning Time"),
                                       execution_time_ms=s["explain"].get("Execution Time"))
                                  for s in diagnostic["statements"]]))
    results = []
    for key, cases in sorted(cells.items()):
        timing = defaultdict(list)
        paired = defaultdict(list)
        diagnostics = defaultdict(list)
        seed_medians = {}
        for case in cases:
            samples = case["samples"]
            baseline = {s["block"]: s["elapsed_ns"] for s in samples if s["arm"] == "original"}
            if len(baseline) != 5:
                raise ValueError("Missing block")
            by_seed = defaultdict(list)
            for sample in samples:
                if not sample["output_verified"]:
                    raise ValueError("Output not verified")
                arm = sample["arm"]
                timing[arm].append(sample["elapsed_ns"])
                by_seed[arm].append(sample["elapsed_ns"])
                paired[arm].append(sample["elapsed_ns"]/baseline[sample["block"]])
            seed_medians[str(case["seed"])] = {a: statistics.median(v) for a, v in by_seed.items()}
            for entry in case["diagnostics"]:
                diagnostics[entry["arm"]].append(entry)
        medians = {a: statistics.median(v)/1e6 for a, v in timing.items()}
        if any(len(v) != 15 for v in timing.values()):
            raise ValueError("Invalid cell sample size")
        best = min(("manual_grouped_join", "manual_correlated_subquery"), key=lambda a: medians[a])
        resources = {}
        for arm, entries in diagnostics.items():
            resources[arm] = dict(
                median_client_to_server_bytes=statistics.median(e["client_to_server_bytes"] for e in entries),
                median_server_to_client_bytes=statistics.median(e["server_to_client_bytes"] for e in entries),
                median_total_stream_bytes=statistics.median(e["client_to_server_bytes"]+e["server_to_client_bytes"] for e in entries),
                median_sum_heap_pool_peaks_bytes=statistics.median(e["memory"]["sum_heap_pool_peaks_bytes"] for e in entries),
                median_peak_sum_minus_before_bytes=statistics.median(e["memory"]["peak_sum_minus_before_bytes"] for e in entries))
        results.append(dict(targets=key[0], density=key[1], page_percent=key[2],
                            samples_per_arm=15, median_ms=medians,
                            median_paired_ratio_to_original={a:statistics.median(v) for a,v in paired.items()},
                            best_observed_manual=best, both_to_original_ratio=medians["both"]/medians["original"],
                            both_to_best_manual_ratio=medians["both"]/medians[best],
                            p2_to_original_ratio=medians["p2"]/medians["original"],
                            both_to_p1_ratio=medians["both"]/medians["p1"],
                            seed_median_ns=seed_medians, resources=resources))
    analysis = dict(status="complete", source_archive=str(source.relative_to(ROOT)),
                    raw_sha256=digest(source/"raw.json"), source_manifest_sha256=digest(source/"manifest.json"),
                    analyzer_sha256=digest(Path(__file__)), cells=results, diagnostic_plan_nodes=scans,
                    memory_diagnostic=dict(calls=144, zero_peak_increment_calls=zero_memory_increments,
                        limitation="Heap-pool usage quantization/retained earlier evidence makes small increments unresolved; zero does not mean no allocation. No process RSS or trustworthy allocation comparison."),
                    limitations=["single local JVM/container", "data seeds are not independent deployment replications",
                                 "best manual baseline selected descriptively from this same matrix",
                                 "timing excludes production source analysis/live binding and connection/transaction setup",
                                 "plans/network/memory collected separately from timing", "no significance or production-speedup claim"])
    destination = ROOT / "evidence/app-db-cost-analysis" / source.name
    destination.mkdir(parents=True, exist_ok=False)
    (destination/"analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2)+"\n")
    (destination/"analyze_app_db_costs.py").write_bytes(Path(__file__).read_bytes())
    (destination/"manifest.json").write_text(json.dumps({p.name:digest(p) for p in sorted(destination.iterdir())},indent=2)+"\n")
    print("n density page% original p1 p2 both best_manual both/manual")
    for cell in results:
        m = cell["median_ms"]
        print(cell["targets"], cell["density"], cell["page_percent"],
              *(f"{m[a]:.3f}" for a in ("original","p1","p2","both",cell["best_observed_manual"])),
              f"{cell['both_to_best_manual_ratio']:.3f}")
    print(json.dumps(dict(output=str(destination), zero_memory_increment_calls=zero_memory_increments)))


if __name__ == "__main__":
    main()
