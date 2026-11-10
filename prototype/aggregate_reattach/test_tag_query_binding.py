"""Live binder validity, one-use lifecycle, guarded fallback, and result checks."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import DatabaseError, connection, transaction

from documents import permissions
from documents.models import Tag
import tag_query_binding as binding
from tag_counts_b2 import compile_tag_counts_b2, read_tag_counts_b0
from test_tag_b2 import CASES, capture, setup_case
from transform_contract import Evidence, REQUIREMENTS, State


def declarations():
    return {name: Evidence(State.DECLARED, "isolated-test:fixed-inputs/no-writers/immediate-consumer")
            for name, state in REQUIREMENTS.items() if state == State.DECLARED}


# pytest-django can move transaction=True tests after other modules. Keep the
# report alive across that split so its final write includes every record.
@pytest.fixture(scope="session")
def report():
    records = []
    yield records
    Path(os.environ["REVERSEDB_RESULTS"], "tag-query-binding.json").write_text(json.dumps({
        "scope": "Live Tag query binding and guarded lowering; no performance claim",
        "records": records,
    }, indent=2, default=str) + "\n")


@pytest.mark.django_db
@pytest.mark.parametrize("name,tags,documents,seed,density,deleted", CASES,
                         ids=[case[0] for case in CASES])
def test_real_bind_lower_execute(name, tags, documents, seed, density, deleted, report):
    assert connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    user, _, expected, _ = setup_case(tags, documents, seed, density, deleted)
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    candidate = binding.bind_tag_query(user, target, assumptions)
    assert candidate.candidate is not None, candidate.plan
    assert candidate.plan["decision"] == "eligible_for_lowering"
    assert (candidate.sql, candidate.params) == compile_tag_counts_b2(user)
    assert read_tag_counts_b0(user) == expected
    (actual, route), queries = capture(lambda: binding.execute_tag_candidate(
        candidate, user, target, assumptions))
    assert actual == expected and route["route"] == "candidate", route
    business = [q for q in queries if q["sql"].startswith("WITH target_rows")]
    assert len(business) == 1
    assert all(type(k) is int and type(v) is int for k, v in actual)
    # Tampering with a retained payload cannot change the SQL used by executor.
    report.append({"kind": "differential", "case": name, "status": "passed",
                   "expected": expected, "actual": actual, "plan": candidate.plan,
                   "facts": candidate.facts, "route": route, "queries_including_checks": queries})


@pytest.mark.django_db
@pytest.mark.parametrize("condition", [k for k, v in REQUIREMENTS.items() if v == State.DECLARED])
def test_missing_declaration_falls_back(condition, report):
    user, _, expected, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    del assumptions[condition]
    candidate = binding.bind_tag_query(user, target, assumptions)
    assert candidate.candidate is None and candidate.plan["decision"] == "needs_evidence"
    actual, route = binding.execute_tag_candidate(candidate, user, target, assumptions)
    assert actual == expected and route["route"] == "original"
    report.append({"kind": "missing_contract", "case": condition, "route": route, "status": "passed"})


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["query", "consumer", "declaration", "profile", "runtime_code", "search_path"])
def test_stale_binding_falls_back(change, report):
    user, _, _, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    candidate = binding.bind_tag_query(user, target, assumptions)
    assert candidate.candidate is not None, candidate.plan
    consumer = binding.CONSUMER
    if change == "query":
        target = target.filter(pk=Tag.objects.order_by("pk").first().pk)
    elif change == "consumer":
        consumer = "different-consumer/v2"
    elif change == "declaration":
        assumptions["fixed_read_state"] = Evidence(State.DECLARED, "a-different-read-scope")
    elif change == "search_path":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL search_path TO public")
    expected = binding.original(target, user)
    if change == "profile":
        profile = json.loads(binding.PROFILE.read_text())
        profile["audit"] = "a-new-audit-profile"
        class ChangedProfile:
            def read_text(self):
                return json.dumps(profile)
        context = patch.object(binding, "PROFILE", ChangedProfile())
    elif change == "runtime_code":
        upstream = permissions.annotate_document_count_by_ids
        def same_behavior_different_code(*args, **kwargs):
            return upstream(*args, **kwargs)
        context = patch.object(permissions, "annotate_document_count_by_ids", same_behavior_different_code)
    else:
        from contextlib import nullcontext
        context = nullcontext()
    with context:
        (actual, route), queries = capture(lambda: binding.execute_tag_candidate(
            candidate, user, target, assumptions, consumer))
    assert actual == expected and route["route"] == "original", route
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    report.append({"kind": "stale_binding", "case": change, "route": route, "status": "passed"})


@pytest.mark.django_db
def test_single_use_and_untrusted_payload(report):
    user, _, expected, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    candidate = binding.bind_tag_query(user, target, assumptions)
    candidate.sql, candidate.params = "SELECT 999, 999", ()
    actual, route = binding.execute_tag_candidate(candidate, user, target, assumptions)
    assert actual == expected and route["route"] == "candidate"
    again, second = binding.execute_tag_candidate(candidate, user, target, assumptions)
    assert again == expected and second["route"] == "original"
    report.append({"kind": "lifecycle", "case": "payload_rebuilt_and_single_use", "status": "passed"})


@pytest.mark.django_db
def test_identity_and_query_fact_injection(report):
    _, ordinary, _, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    candidate = binding.bind_tag_query(ordinary, target, assumptions)
    actual, route = binding.execute_tag_candidate(candidate, ordinary, target, assumptions)
    assert actual == binding.original(target, ordinary) and route["route"] == "original"
    assumptions["group_key_unique"] = Evidence(State.VERIFIED, "caller-forged-fact")
    with pytest.raises(ValueError):
        binding.bind_tag_query(ordinary, target, assumptions)
    report.append({"kind": "guard", "case": "identity_and_external_verified_rejected", "status": "passed"})


@pytest.mark.django_db
def test_candidate_sql_failure_does_not_retry_original(report):
    user, _, _, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    candidate = binding.bind_tag_query(user, target, assumptions)
    def fault(execute, sql, params, many, context):
        if sql.startswith("WITH target_rows"):
            raise DatabaseError("synthetic candidate failure")
        return execute(sql, params, many, context)
    with patch.object(binding, "original", wraps=binding.original) as original:
        with connection.execute_wrapper(fault), pytest.raises(DatabaseError, match="synthetic candidate failure"):
            binding.execute_tag_candidate(candidate, user, target, assumptions)
        original.assert_not_called()
    report.append({"kind": "guard", "case": "query_failure_propagates_without_retry", "status": "passed"})


@pytest.mark.django_db(transaction=True)
def test_transaction_boundary_invalidates_candidate(report):
    user, _, expected, _ = setup_case(4, 3, 42, 0.5, "some")
    target, assumptions = Tag.objects.order_by("pk"), declarations()
    with transaction.atomic():
        candidate = binding.bind_tag_query(user, target, assumptions)
        assert candidate.candidate is not None, candidate.plan
    with transaction.atomic():
        actual, route = binding.execute_tag_candidate(candidate, user, target, assumptions)
    assert actual == expected and route["route"] == "original", route
    report.append({"kind": "stale_binding", "case": "new_transaction", "route": route, "status": "passed"})
