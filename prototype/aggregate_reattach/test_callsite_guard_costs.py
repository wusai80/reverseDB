"""One fixed duplicate-guard optimization and a bounded before/after screen."""
import hashlib
import json
import os
import random
import statistics
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db import DatabaseError, connection
from django.test import override_settings

import callsite_binding as after
import callsite_binding_before as before
import tag_query_binding as runtime
from test_callsite_binding import extracted, setup
from test_tag_b2 import capture

BEFORE_SHA = "6c542df2dbb9388fe3416f21d78a7e0e89594a7790af43a54c322c188e847754"
CASES = [
    ("tag", "mixed", (6, 9, 42, 0.5, "some")),
    ("tag", "dense", (30, 80, 43, 0.8, "some")),
    ("custom_field", "mixed", (5, 7, 0.6, "mixed", 43)),
    ("custom_field", "dense", (25, 60, 0.8, "mixed", 44)),
]
BLOCKS, ROUNDS, WARMUPS = 5, 5, 2


@pytest.fixture(scope="session")
def results():
    assert hashlib.sha256(Path(before.__file__).read_bytes()).hexdigest() == BEFORE_SHA
    result = {"scope": "Single duplicate runtime guard removal", "guards": [], "costs": [],
              "before_sha256": BEFORE_SHA,
              "protocol": {"blocks": BLOCKS, "rounds": ROUNDS, "warmups": WARMUPS,
                           "order_seed": 20261104, "timed_trace": False,
                           "boundary": "fresh QuerySet + candidate bind + guarded execution + full result",
                           "extraction_input": "pre-extracted configuration; both paths revalidate source",
                           "replication": "temporal blocks in one process/database, not independent deployments",
                           "cache": "warm; no binding/schema/result cache", "concurrency": 1}}
    yield result
    Path(os.environ["REVERSEDB_RESULTS"], "tag-guard-costs.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["query", "declaration", "manager", "profile", "sql_payload", "token"])
def test_changed_runtime_still_checked_once(key, change, results):
    user, target, _, declarations = setup(key)
    candidate = after.bind_callsite(extracted(key), user, target, declarations)
    assert candidate.fingerprint is not None
    context = nullcontext()
    if change == "query":
        target = target.filter(pk=target.first().pk)
    elif change == "declaration":
        del declarations["fixed_read_state"]
    elif change == "manager":
        spec = runtime.SPECS[key]
        original = spec.manager_class.get_queryset
        def changed(manager):
            qs = original(manager)
            return qs.filter(pk__lt=0) if manager.model is spec.link_model else qs
        context = patch.object(spec.manager_class, "get_queryset", changed)
    elif change == "profile":
        attr = "PROFILE" if key == "tag" else "CUSTOM_PROFILE"
        profile = json.loads(getattr(runtime, attr).read_text())
        profile["audit_revision"] = "changed"
        context = patch.object(runtime, attr, SimpleNamespace(read_text=lambda: json.dumps(profile)))
    elif change == "sql_payload":
        candidate.binding.sql, candidate.binding.params = "SELECT 999, 999", ()
    else:
        candidate.binding.candidate.fingerprint = "altered-token"
    with context:
        expected = runtime.original_for(key, target, user)
        (actual, route), queries = capture(lambda: after.execute_callsite(
            candidate, user, target, declarations, fallback=lambda: runtime.original_for(key, target, user)))
    assert actual == expected
    assert route["route"] == ("candidate" if change == "sql_payload" else "original")
    business = [q for q in queries if q["sql"].startswith("WITH target_rows")]
    assert len(business) == (1 if change == "sql_payload" else 0)
    results["guards"].append({"relation": key, "case": change, "status": "passed", "route": route})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
def test_business_fault_does_not_retry(key, results):
    user, target, _, declarations = setup(key)
    candidate = after.bind_callsite(extracted(key), user, target, declarations)
    fallback = Mock(side_effect=lambda: runtime.original_for(key, target, user))
    def fail(execute, sql, params, many, context):
        if sql.startswith("WITH target_rows"):
            raise DatabaseError("fixed guard-cost fault")
        return execute(sql, params, many, context)
    with connection.execute_wrapper(fail), pytest.raises(DatabaseError, match="fixed guard-cost fault"):
        after.execute_callsite(candidate, user, target, declarations, fallback=fallback)
    fallback.assert_not_called()
    assert candidate.used and candidate.binding.candidate.used
    results["guards"].append({"relation": key, "case": "fault_no_retry", "status": "passed"})


def run_cost_screen(key, name, args, results, baseline=before, bind_counts=(3, 2), query_counts=None):
    user, _, expected, declarations = setup(key, args)
    config = extracted(key)
    model = runtime.SPECS[key].target_model
    def run(module):
        target = model.objects.order_by("pk")
        candidate = module.bind_callsite(config, user, target, declarations)
        return module.execute_callsite(candidate, user, target, declarations,
            fallback=lambda: runtime.original_for(key, target, user))
    methods = {"before": lambda: run(baseline), "after": lambda: run(after),
               "original": lambda: (runtime.original_for(key, model.objects.order_by("pk"), user),
                                     {"route": "original"})}
    record = {"relation": key, "case": name, "fixture_args": args, "methods": {}, "order": []}
    with override_settings(DEBUG=False):
        for label, function in methods.items():
            with patch.object(baseline.runtime if label == "before" else runtime, "bind_count_query",
                              wraps=(baseline.runtime if label == "before" else runtime).bind_count_query) as binder:
                (actual, route), queries = capture(function)
            assert actual == expected and route["route"] == ("original" if label=="original" else "candidate")
            record["methods"][label] = {"bind_calls": binder.call_count, "queries": queries,
                "query_count": len(queries), "samples_ns": [], "block_medians_ns": []}
        old, new = record["methods"]["before"], record["methods"]["after"]
        assert (old["bind_calls"], new["bind_calls"]) == bind_counts
        old_business = [q for q in old["queries"] if q["sql"].startswith("WITH target_rows")]
        new_business = [q for q in new["queries"] if q["sql"].startswith("WITH target_rows")]
        assert len(old_business) == 1 and old_business == new_business
        # Apart from the one unchanged business query, each full bind issues the
        # same catalog/context checks. Exact traces above remain inspectable.
        if query_counts is None:
            assert (old["query_count"] - 1) * bind_counts[1] == (new["query_count"] - 1) * bind_counts[0]
        else:
            assert (old["query_count"], new["query_count"]) == query_counts
        for _ in range(WARMUPS):
            for function in methods.values():
                assert function()[0] == expected
        rng = random.Random(20261104)
        for block in range(BLOCKS):
            block_samples = {m: [] for m in methods}
            for round_index in range(ROUNDS):
                order = list(methods)
                rng.shuffle(order)
                record["order"].append({"block": block, "round": round_index, "methods": order})
                for label in order:
                    assert not connection.execute_wrappers and not connection.queries_logged
                    started = time.perf_counter_ns()
                    actual, route = methods[label]()
                    elapsed = time.perf_counter_ns() - started
                    assert actual == expected and route["route"] == ("original" if label=="original" else "candidate")
                    record["methods"][label]["samples_ns"].append(elapsed)
                    block_samples[label].append(elapsed)
            for label in methods:
                record["methods"][label]["block_medians_ns"].append(statistics.median(block_samples[label]))
        for method in record["methods"].values():
            method["median_ns"] = statistics.median(method["samples_ns"])
    record["status"] = "passed"
    results["costs"].append(record)


@pytest.mark.django_db
@pytest.mark.parametrize("key,name,args", CASES, ids=[k+":"+n for k,n,_ in CASES])
def test_fixed_guard_cost_screen(key, name, args, results):
    run_cost_screen(key, name, args, results)
