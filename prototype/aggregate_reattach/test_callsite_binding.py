"""Fixed two-candidate admission, comparison and provenance checks."""
import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection

import callsite_binding as adapter
import tag_query_binding as runtime
from callsite_extraction import extract_callsites
from test_binding_reuse import CASES as FIELD_CASES, setup_fields
from test_tag_b2 import CASES as TAG_CASES, capture, setup_case
from test_tag_query_binding import declarations


@pytest.fixture(scope="session")
def report():
    records = []
    yield records
    Path(os.environ["REVERSEDB_RESULTS"], "tag-callsites.json").write_text(json.dumps({
        "scope": "Two extracted configurations bound to the audited pk-ordered region",
        "consumer_admission": "not_established", "records": records,
    }, indent=2, default=str) + "\n")


def extracted(key):
    name = "TagViewSet" if key == "tag" else "CustomFieldViewSet"
    return next(c for c in extract_callsites(adapter.VIEWS.read_text())["candidates"]
                if c["class"] == name)


def setup(key, args=None):
    assert connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    if key == "tag":
        user, _, expected, _ = setup_case(*(args or (6, 9, 42, 0.5, "some")))
    else:
        user, expected = setup_fields(*(args or (5, 7, 0.6, "mixed", 43)))
    target = runtime.SPECS[key].target_model.objects.order_by("pk")
    return user, target, expected, declarations()


SCENARIOS = [("tag", c) for c in TAG_CASES] + [("custom_field", c) for c in FIELD_CASES]


@pytest.mark.django_db
@pytest.mark.parametrize("key,case", SCENARIOS, ids=[k+":"+c[0] for k, c in SCENARIOS])
def test_candidate_matches_manual_and_original(key, case, report):
    user, target, expected, assumptions = setup(key, case[1:])
    candidate = adapter.bind_callsite(extracted(key), user, target, assumptions)
    manual = runtime.bind_count_query(key, user, target, assumptions)
    assert candidate.fingerprint is not None, candidate.plan
    assert candidate.spec_key == key
    assert candidate.binding.facts == manual.facts
    assert (candidate.binding.sql, candidate.binding.params) == (manual.sql, manual.params)
    assert candidate.facts["helper_attname"] == manual.facts["relation_spec"]["group_key"]
    baseline = runtime.original_for(key, target, user)
    manual_rows, manual_route = runtime.execute_count_candidate(key, manual, user, target, assumptions)
    (rows, route), queries = capture(lambda: adapter.execute_callsite(
        candidate, user, target, assumptions, fallback=lambda: runtime.original_for(key, target, user)))
    assert rows == manual_rows == baseline == expected
    assert route["route"] == manual_route["route"] == "candidate"
    assert all(type(pk) is int and type(count) is int for pk, count in rows)
    assert len([q for q in queries if q["sql"].startswith("WITH target_rows")]) == 1
    report.append({"kind": "differential", "relation": key, "case": case[0], "status": "passed",
                   "extracted": candidate.extracted, "facts": candidate.facts,
                   "sql": candidate.binding.sql, "params": candidate.binding.params,
                   "manual_facts_equal": True, "manual_sql_and_params_equal": True,
                   "baseline": baseline, "manual_rows": manual_rows, "actual": rows,
                   "expected": expected, "route": route})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["unknown", "tampered_key", "source_revision", "missing_contract",
                                    "consumer", "name_order"])
def test_unproven_configuration_uses_original(key, change, report):
    user, target, _, assumptions = setup(key)
    config, consumer = deepcopy(extracted(key)), runtime.CONSUMER
    if change == "unknown":
        config["class"] = "UnknownViewSet"
    elif change == "tampered_key":
        config["configuration"]["document_count_source_field"] = "document_id"
    elif change == "missing_contract":
        del assumptions["fixed_read_state"]
    elif change == "consumer":
        consumer = "actual-viewset-api/unadmitted"
    elif change == "name_order":
        target = target.order_by("name")
    from contextlib import nullcontext
    context = (patch.object(adapter, "VIEWS", SimpleNamespace(read_text=lambda: "# changed source"))
               if change == "source_revision" else nullcontext())
    baseline = runtime.original_for(key, target, user)
    with context:
        candidate = adapter.bind_callsite(config, user, target, assumptions, consumer)
        assert candidate.fingerprint is None, candidate.plan
        (rows, route), queries = capture(lambda: adapter.execute_callsite(
            candidate, user, target, assumptions, consumer=consumer,
            fallback=lambda: runtime.original_for(key, target, user)))
    assert rows == baseline and route["route"] == "original"
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    report.append({"kind": "admission_guard", "relation": key, "case": change, "status": "passed",
                   "plan": candidate.plan, "route": route})


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["source_after_bind", "second_use"])
def test_execution_provenance_and_lifecycle(key, change, report):
    user, target, expected, assumptions = setup(key)
    candidate = adapter.bind_callsite(extracted(key), user, target, assumptions)
    assert candidate.fingerprint is not None, candidate.plan
    def execute():
        return adapter.execute_callsite(candidate, user, target, assumptions,
            fallback=lambda: runtime.original_for(key, target, user))
    if change == "source_after_bind":
        with patch.object(adapter, "VIEWS", SimpleNamespace(read_text=lambda: "# changed source")):
            (rows, route), queries = capture(execute)
    else:
        rows, route = execute()
        assert rows == expected and route["route"] == "candidate"
        (rows, route), queries = capture(execute)
    assert rows == expected and route["route"] == "original"
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    report.append({"kind": "execution_guard", "relation": key, "case": change,
                   "status": "passed", "route": route})
