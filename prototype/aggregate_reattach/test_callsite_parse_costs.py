"""Exact-text syntax reuse; database admission is always fresh."""
import cProfile
import hashlib
import io
import json
import os
import pstats
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import callsite_binding as after
import callsite_binding_uncached as before
import tag_query_binding as runtime
from test_callsite_binding import extracted, setup
from test_callsite_guard_costs import CASES, BLOCKS, ROUNDS, WARMUPS, run_cost_screen
from test_tag_b2 import capture

BEFORE_SHA = "b583f9ecc44c155e99ec61d7ea20692f5c22c82fa416da3a2918307f6a3b74b2"


@pytest.fixture(scope="session")
def results():
    assert hashlib.sha256(Path(before.__file__).read_bytes()).hexdigest() == BEFORE_SHA
    result = {"scope": "Exact-source syntax reuse without admission caching", "guards": [], "costs": [],
              "before_sha256": BEFORE_SHA,
              "protocol": {"blocks": BLOCKS, "rounds": ROUNDS, "warmups": WARMUPS,
                           "order_seed": 20261104, "timed_trace": False,
                           "boundary": "fresh QuerySet + candidate bind + guarded execution + full result",
                           "replication": "temporal blocks in one process/database, not independent deployments",
                           "cache": "one exact source text and immutable syntax result; warm timing",
                           "cold_behavior": "separate correctness and parse-count checks, not cold latency benchmark"}}
    yield result
    Path(os.environ["REVERSEDB_RESULTS"], "tag-parse-costs.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")


def execute(module, config, key, user, target, declarations):
    candidate = module.bind_callsite(config, user, target, declarations)
    assert candidate.fingerprint is not None, candidate.plan
    return module.execute_callsite(candidate, user, target, declarations,
        fallback=lambda: runtime.original_for(key, target, user))


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["source_changed", "candidate_mutated", "cold_warm"])
def test_syntax_reuse_boundaries(key, change, results):
    user, target, expected, declarations = setup(key)
    config = extracted(key)
    after._parse_source.cache_clear()
    with patch.object(after, "extract_callsites", wraps=after.extract_callsites) as parser:
        (rows, route), queries = capture(lambda: execute(after, config, key, user, target, declarations))
    assert rows == expected and route["route"] == "candidate" and parser.call_count == 1
    assert len(queries) == 17
    candidate = after.bind_callsite(config, user, target, declarations)
    if change == "source_changed":
        with patch.object(after, "VIEWS", SimpleNamespace(read_text=lambda: "# changed source")):
            rows, route = after.execute_callsite(candidate, user, target, declarations,
                fallback=lambda: runtime.original_for(key, target, user))
        assert rows == expected and route["route"] == "original"
    elif change == "candidate_mutated":
        candidate.extracted["configuration"]["document_count_source_field"] = "document_id"
        rows, route = after.execute_callsite(candidate, user, target, declarations,
            fallback=lambda: runtime.original_for(key, target, user))
        assert rows == expected and route["route"] == "original"
        # Mutating one caller's candidate cannot poison the retained syntax.
        rows, route = execute(after, config, key, user, target, declarations)
        assert rows == expected and route["route"] == "candidate"
    else:
        with patch.object(after, "extract_callsites", wraps=after.extract_callsites) as parser:
            (rows, route), queries = capture(lambda: execute(after, config, key, user, target, declarations))
        assert rows == expected and route["route"] == "candidate"
        assert parser.call_count == 0 and len(queries) == 17
    results["guards"].append({"relation": key, "case": change, "status": "passed"})


def test_exact_text_key_and_bounded_immutable_result(results):
    source = after.VIEWS.read_text()
    after._parse_source.cache_clear()
    value = after._parse_source(source)
    assert isinstance(value, str)
    mutable_copy = json.loads(value)
    mutable_copy["candidates"].clear()
    assert json.loads(after._parse_source(source))["candidates"]
    altered = source + "\n# distinct source text\n"
    assert json.loads(after._parse_source(altered))["source_sha256"] != json.loads(value)["source_sha256"]
    info = after._parse_source.cache_info()
    assert info.currsize == info.maxsize == 1 and info.misses == 2
    after._parse_source.cache_clear()
    results["guards"].append({"case": "exact_text_bounded_immutable", "status": "passed"})


def test_parse_errors_are_not_cached(results):
    after._parse_source.cache_clear()
    with patch.object(after, "extract_callsites", wraps=after.extract_callsites) as parser:
        for _ in range(2):
            with pytest.raises(SyntaxError):
                after._parse_source("def (")
    assert parser.call_count == 2 and after._parse_source.cache_info().currsize == 0
    results["guards"].append({"case": "errors_not_cached", "status": "passed"})


@pytest.mark.django_db
@pytest.mark.parametrize("key,name,args", CASES, ids=[k+":"+n for k,n,_ in CASES])
def test_fixed_parse_cost_screen(key, name, args, results):
    after._parse_source.cache_clear()
    run_cost_screen(key, name, args, results, baseline=before, bind_counts=(2, 2))
    record = results["costs"][-1]
    assert record["methods"]["before"]["queries"] == record["methods"]["after"]["queries"]
    # Separate profiling only; its timing is never mixed with uninstrumented samples.
    from django.contrib.auth.models import User
    from test_tag_query_binding import declarations
    user = User.objects.get(is_superuser=True)
    config = extracted(key)
    for label, module in (("before", before), ("after", after)):
        target = runtime.SPECS[key].target_model.objects.order_by("pk")
        profile = cProfile.Profile()
        with patch.object(module, "extract_callsites", wraps=module.extract_callsites) as parser:
            profile.enable()
            rows, route = execute(module, config, key, user, target, declarations())
            profile.disable()
        assert route["route"] == "candidate"
        assert parser.call_count == (2 if label == "before" else 0)
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(20)
        record["methods"][label]["profile_separate"] = stream.getvalue()
        record["methods"][label]["warm_parse_calls"] = parser.call_count
