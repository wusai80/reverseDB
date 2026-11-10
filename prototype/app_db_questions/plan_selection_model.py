"""Exact enumeration of an illustrative finite AND-OR plan space.

Costs/capacities are invented units, not database measurements. All alternative
edges are ASSUMED equivalent in this model; this does not prove equivalence.
"""
import hashlib
import itertools
import json
from pathlib import Path


EDGES = {
    "direct_a": dict(output="a", inputs=[], cost=6, capacity=0),
    "direct_b": dict(output="b", inputs=[], cost=6, capacity=0),
    "shared": dict(output="s", inputs=[], cost=8, capacity=3),
    "a_from_s": dict(output="a", inputs=["s"], cost=1, capacity=0),
    "b_from_s": dict(output="b", inputs=["s"], cost=1, capacity=0),
}


def evaluate(selected, required, budget):
    edges = [EDGES[name] for name in selected]
    outputs = [edge["output"] for edge in edges]
    if len(outputs) != len(set(outputs)) or not set(required) <= set(outputs):
        return None
    if any(not set(edge["inputs"]) <= set(outputs) for edge in edges):
        return None
    capacity = sum(edge["capacity"] for edge in edges)
    if budget is not None and capacity > budget:
        return None
    return dict(selected=sorted(selected), cost=sum(edge["cost"] for edge in edges), capacity=capacity)


def solve(required, budget):
    feasible = []
    for bits in itertools.product((False, True), repeat=len(EDGES)):
        selected = [name for name, chosen in zip(EDGES, bits) if chosen]
        plan = evaluate(selected, required, budget)
        if plan is not None:
            feasible.append(plan)
    best = min(feasible, key=lambda p: (p["cost"], p["capacity"], p["selected"]), default=None)
    return dict(required=required, capacity_budget=budget, searched=2**len(EDGES),
                feasible=len(feasible), best=best)


def run():
    # This hardcoded candidate graph is acyclic: s precedes a/b.
    cases = [solve(["a", "b"], None), solve(["a", "b"], 2),
             solve(["a", "b"], 3), solve(["a"], None)]
    expected = [(10, ["a_from_s", "b_from_s", "shared"]),
                (12, ["direct_a", "direct_b"]),
                (10, ["a_from_s", "b_from_s", "shared"]), (6, ["direct_a"])]
    for case, (cost, selected) in zip(cases, expected):
        if case["best"]["cost"] != cost or case["best"]["selected"] != selected:
            raise AssertionError(case)
    invalid = {
        "missing_shared_dependency": ["a_from_s", "b_from_s"],
        "missing_required_output": ["direct_a"],
    }
    for selected in invalid.values():
        if evaluate(selected, ["a", "b"], None) is not None:
            raise AssertionError(selected)
    return dict(status="passed", scope="finite synthetic planning model; no SQL or performance experiment",
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                edges=EDGES, cases=cases, rejected_plans=invalid)


if __name__ == "__main__":
    report = run()
    root = Path(__file__).resolve().parents[2]
    output = root / "evidence/app-db-planning/20261104/selection-model.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(status=report["status"], cases=len(report["cases"]),
                         optimal_costs=[c["best"]["cost"] for c in report["cases"]],
                         rejected_plans=len(report["rejected_plans"]))))
