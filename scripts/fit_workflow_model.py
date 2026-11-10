#!/usr/bin/env python3
"""Fit one tiny nonnegative model on the designated calibration seed only."""
import hashlib
import itertools
import json
import math
import statistics
import sys
from pathlib import Path


def solve(matrix, vector):
    n = len(vector)
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [v / scale for v in rows[column]]
        for i in range(n):
            if i != column:
                scale = rows[i][column]
                rows[i] = [a-scale*b for a, b in zip(rows[i], rows[column])]
    return [row[-1] for row in rows]


def fit(x, y):
    columns = len(x[0])
    best = None
    for bits in itertools.product((False, True), repeat=columns):
        active = [i for i, included in enumerate(bits) if included]
        coefficients = solve([[sum(row[i]*row[j] for row in x) for j in active] for i in active],
                             [sum(row[i]*target for row, target in zip(x, y)) for i in active]) if active else []
        if coefficients is None or any(v < -1e-9 for v in coefficients):
            continue
        weights = [0.0]*columns
        for i, value in zip(active, coefficients):
            weights[i] = max(0.0, value)
        loss = sum((sum(a*b for a, b in zip(row, weights))-target)**2 for row, target in zip(x, y))
        candidate = (loss, len(active), weights)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError("No feasible calibration model")
    return best[2], best[0]


def main():
    source, destination = map(Path, sys.argv[1:])
    raw = json.loads(source.read_text())
    if raw["status"] != "passed" or raw["stage"] != "calibrate" or raw["timed_samples"] != 120 or len(raw["semantic_checks"]) != 17:
        raise ValueError("Incomplete calibration/semantics")
    if {case["seed"] for case in raw["cases"]} != {42}:
        raise ValueError("Evaluation data must not enter calibration")
    x, y, points = [], [], []
    for case in raw["cases"]:
        for arm in raw["protocol"]["direct_arms"]:
            timings = [s["elapsed_ns"]/1e6 for s in case["samples"] if s["arm"] == arm]
            if len(timings) != 5:
                raise ValueError("Wrong calibration block count")
            vector = case["features"][arm]["vector"]
            target = statistics.median(timings)
            x.append(vector)
            y.append(target)
            points.append(dict(page_size=case["page_size"], overlap_percent=case["overlap_percent"], arm=arm, observed_median_ms=target))
    scales = [max(row[i] for row in x) or 1 for i in range(4)]
    normalized = [[value/scale for value, scale in zip(row, scales)] for row in x]
    weights, loss = fit(normalized, y)
    for point, row in zip(points, normalized):
        point["predicted_ms"] = sum(a*b for a, b in zip(row, weights))
    model = dict(status="frozen_before_evaluation", scope="small calibrated template chooser, not source inference",
                 calibration_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 fitter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 features=raw["protocol"]["selector"]["features"], weights=weights, scales=scales,
                 calibration_seed=42, calibration_points=points, calibration_rmse_ms=math.sqrt(loss/len(y)),
                 method="exhaustive active-set NNLS with four nonnegative coefficients and no intercept",
                 caveat="No claim of transfer to unseen workload shapes, concurrency, or production binders")
    destination.write_text(json.dumps(model, indent=2)+"\n")
    print(json.dumps(dict(points=len(points), weights=weights, rmse_ms=model["calibration_rmse_ms"])))


if __name__ == "__main__":
    main()
