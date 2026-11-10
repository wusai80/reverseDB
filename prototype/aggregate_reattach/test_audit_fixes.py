"""Regression tests for the three confirmed code-audit findings."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db.models import Value

import callsite_binding as adapter
import tag_query_binding as runtime
from test_callsite_binding import extracted, setup
from test_tag_b2 import capture


@pytest.fixture(scope="session")
def report():
    records = []
    yield records
    Path(os.environ["REVERSEDB_RESULTS"], "tag-audit-fixes.json").write_text(json.dumps({
        "scope": "Regression fixes for code audit P1 and two P2 findings",
        "records": records,
    }, indent=2, default=str) + "\n")


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("phase", ["bind", "execute"])
@pytest.mark.parametrize("shape", ["extra", "annotation", "alias", "projection"])
def test_unadmitted_query_structure_preserves_original(key, phase, shape, report):
    user, target, _, declarations = setup(key)
    config = extracted(key)
    candidate = adapter.bind_callsite(config, user, target, declarations) if phase == "execute" else None
    if candidate:
        assert candidate.fingerprint is not None, candidate.plan
    if shape == "extra":
        target = target.extra(select={"document_count": "%s"}, select_params=[999])
    elif shape == "annotation":
        target = target.annotate(audit_probe=Value(7))
    elif shape == "alias":
        target = target.alias(audit_probe=Value(7))
    else:
        target = target.values("pk")
    expected = runtime.original_for(key, target, user)
    if shape == "extra":
        assert expected and all(count == 999 for _, count in expected)
    if phase == "bind":
        candidate = adapter.bind_callsite(config, user, target, declarations)
        assert candidate.fingerprint is None and candidate.plan["action"] == "keep_original"
    fallback = Mock(side_effect=lambda: runtime.original_for(key, target, user))
    (rows, route), queries = capture(lambda: adapter.execute_callsite(
        candidate, user, target, declarations, fallback=fallback))
    assert rows == expected and route["route"] == "original"
    fallback.assert_called_once_with()
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    report.append({"kind": "P1_query_shape", "relation": key, "phase": phase, "shape": shape,
                   "status": "passed", "expected": expected, "actual": rows, "route": route})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["query", "consumer", "declaration"])
def test_inner_rejection_uses_caller_fallback(key, change, report):
    user, target, _, declarations = setup(key)
    candidate = adapter.bind_callsite(extracted(key), user, target, declarations)
    assert candidate.fingerprint is not None
    consumer = runtime.CONSUMER
    if change == "query":
        target = target.filter(pk=target.first().pk)
    elif change == "consumer":
        consumer = "unadmitted-consumer"
    else:
        del declarations["fixed_read_state"]
    sentinel = object()
    fallback = Mock(return_value=sentinel)
    (rows, route), queries = capture(lambda: adapter.execute_callsite(
        candidate, user, target, declarations, fallback=fallback, consumer=consumer))
    assert rows is sentinel and route["route"] == "original"
    fallback.assert_called_once_with()
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    report.append({"kind": "P2_caller_fallback", "relation": key, "change": change,
                   "status": "passed", "callback_calls": fallback.call_count, "route": route})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("phase", ["bind", "execute"])
@pytest.mark.parametrize("problem", ["malformed_json", "missing_fields", "wrong_type", "converter_schema",
                                     "missing_file", "invalid_utf8"])
def test_bad_profile_rejects_and_calls_original(key, phase, problem, report):
    user, target, expected, declarations = setup(key)
    config = extracted(key)
    candidate = adapter.bind_callsite(config, user, target, declarations) if phase == "execute" else None
    if candidate:
        assert candidate.fingerprint is not None
    attr = "PROFILE" if key == "tag" else "CUSTOM_PROFILE"
    content = json.loads(getattr(runtime, attr).read_text())
    if problem == "malformed_json":
        reader = Mock(return_value="{")
    elif problem == "missing_fields":
        reader = Mock(return_value="{}")
    elif problem == "wrong_type":
        content["postgres_version"] = "180006"
        reader = Mock(return_value=json.dumps(content))
    elif problem == "converter_schema":
        content["converter_files"] = []
        reader = Mock(return_value=json.dumps(content))
    elif problem == "missing_file":
        reader = Mock(side_effect=FileNotFoundError("audit profile missing"))
    else:
        reader = Mock(side_effect=UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid byte"))
    fallback = Mock(return_value=expected)
    with patch.object(runtime, attr, SimpleNamespace(read_text=reader)):
        if phase == "bind":
            # Cover direct callers as well as the source-adapter wrapper.
            direct = runtime.bind_count_query(key, user, target, declarations)
            assert direct.candidate is None and direct.plan["action"] == "keep_original"
            candidate = adapter.bind_callsite(config, user, target, declarations)
            assert candidate.fingerprint is None
        (rows, route), queries = capture(lambda: adapter.execute_callsite(
            candidate, user, target, declarations, fallback=fallback))
    assert rows == expected and route["route"] == "original"
    fallback.assert_called_once_with()
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    decision = candidate.plan if phase == "bind" else route["current_decision"]
    assert decision["decision"] == "rejected" and "audit profile" in decision["reason"]
    report.append({"kind": "P2_invalid_profile", "relation": key, "phase": phase, "problem": problem,
                   "status": "passed", "decision": decision, "callback_calls": fallback.call_count})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
def test_fallback_exception_propagates_without_retry(key, report):
    user, target, _, declarations = setup(key)
    candidate = adapter.bind_callsite(extracted(key), user, target, declarations)
    assert candidate.fingerprint is not None
    fallback = Mock(side_effect=RuntimeError("original path failure"))
    with pytest.raises(RuntimeError, match="original path failure"):
        adapter.execute_callsite(candidate, user, target, declarations,
                                 fallback=fallback, consumer="unadmitted-consumer")
    fallback.assert_called_once_with()
    report.append({"kind": "P2_fallback_error", "relation": key, "status": "passed",
                   "callback_calls": fallback.call_count})
