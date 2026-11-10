"""Second model relation, sharing the same recovered helper and binder/lowerer."""
import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django_softdelete.managers import SoftDeleteManager

from documents.models import CustomField, CustomFieldInstance, Document, Tag
import tag_query_binding as binding
from test_tag_b2 import capture
from test_tag_query_binding import declarations


@pytest.fixture(scope="module")
def report():
    records = []
    yield records
    Path(os.environ["REVERSEDB_RESULTS"], "tag-reuse.json").write_text(json.dumps({
        "scope": "Two audited relation specs; CustomField reuse plus Tag regression",
        "records": records,
    }, indent=2, default=str) + "\n")


def setup_fields(nfields=5, ndocs=7, density=0.6, deletion="mixed", seed=43):
    assert not CustomField.objects.exists()
    user = User.objects.create(username="reuse-superuser", is_active=True, is_superuser=True)
    instant = datetime(2026, 11, 4, tzinfo=timezone.utc)
    fields = [CustomField.objects.create(name=f"custom-{i}", data_type="integer",
                                         created=instant-timedelta(days=i)) for i in range(nfields)]
    expected = {field.pk: 0 for field in fields}
    documents = [Document.objects.create(title=f"reuse-{i}", owner=user,
                 checksum=hashlib.md5(f"reuse-{i}".encode()).hexdigest(), mime_type="application/pdf")
                 for i in range(ndocs)]
    rng = random.Random(seed)
    rows, deleted_docs = [], []
    for i, document in enumerate(documents):
        doc_deleted = deletion == "all_documents" or (deletion == "mixed" and i % 3 == 0)
        if doc_deleted:
            deleted_docs.append(document.pk)
        for j, field in enumerate(fields):
            if rng.random() >= density:
                continue
            link_deleted = deletion == "all_links" or (deletion == "mixed" and (i+j) % 4 == 0)
            rows.append(CustomFieldInstance(field=field, document=document, value_int=None,
                                            deleted_at=instant if link_deleted else None))
            if not link_deleted and not doc_deleted:
                expected[field.pk] += 1
    CustomFieldInstance.objects.bulk_create(rows)
    Document.objects.filter(pk__in=deleted_docs).update(deleted_at=instant)
    return user, sorted(expected.items())


CASES = [
    ("empty", 0, 0, 0, "none", 42),
    ("no_documents", 4, 0, 0, "none", 42),
    ("no_links", 4, 3, 0, "none", 42),
    ("all_links_deleted", 4, 3, 1, "all_links", 42),
    ("all_documents_deleted", 4, 3, 1, "all_documents", 42),
    ("two_deletion_layers_and_null_payload", 5, 7, 0.6, "mixed", 43),
    ("sparse", 20, 40, 0.1, "mixed", 43),
    ("dense", 25, 60, 0.8, "mixed", 44),
]


@pytest.mark.django_db
@pytest.mark.parametrize("name,nfields,ndocs,density,deletion,seed", CASES, ids=[x[0] for x in CASES])
def test_custom_field_shared_pipeline(name, nfields, ndocs, density, deletion, seed, report):
    assert connection.settings_dict["HOST"] == "db" and connection.settings_dict["NAME"].startswith("test_")
    user, expected = setup_fields(nfields, ndocs, density, deletion, seed)
    target, assumptions = CustomField.objects.order_by("pk"), declarations()
    baseline, source_queries = capture(lambda: binding.original_for("custom_field", target, user))
    candidate = binding.bind_count_query("custom_field", user, target, assumptions)
    assert candidate.candidate is not None, candidate.plan
    assert f'counts AS ({source_queries[0]["sql"]})' in candidate.sql
    assert tuple(source_queries[0]["params"]) == candidate.params
    assert 'c."field_id" = t."pk"' in candidate.sql
    actual, route = binding.execute_count_candidate("custom_field", candidate, user, target, assumptions)
    assert actual == baseline == expected and route["route"] == "candidate"
    assert all(type(k) is int and type(n) is int for k, n in actual)
    report.append({"kind": "differential", "case": name, "status": "passed",
                   "baseline": baseline, "actual": actual, "expected": expected,
                   "source_queries": source_queries, "sql": candidate.sql, "params": candidate.params,
                   "facts": candidate.facts, "plan": candidate.plan, "route": route})


@pytest.mark.django_db
def test_wrong_manager_and_value_column_are_detected(report):
    user, expected = setup_fields(4, 5, 1, "mixed")
    target = CustomField.objects.order_by("pk")
    candidate = binding.bind_count_query("custom_field", user, target, declarations())
    assert candidate.candidate is not None, candidate.plan
    assert binding.original_for("custom_field", target, user) == expected
    marker = '"documents_customfieldinstance"."deleted_at" IS NULL'
    count_marker = 'COUNT(DISTINCT "documents_customfieldinstance"."document_id")'
    assert candidate.sql.count(marker) == candidate.sql.count(count_marker) == 1
    mutations = {
        "omit_relation_soft_delete": candidate.sql.replace(marker, "TRUE"),
        "count_nullable_payload": candidate.sql.replace(count_marker,
            'COUNT(DISTINCT "documents_customfieldinstance"."value_int")'),
    }
    for name, sql in mutations.items():
        with connection.cursor() as cursor:
            cursor.execute(sql, candidate.params)
            wrong = cursor.fetchall()
        assert wrong != expected, name
        report.append({"kind": "negative_control", "case": name, "status": "detected",
                       "expected": expected, "wrong": wrong, "sql": sql})


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["remove_delete_filter", "add_extra_filter"])
def test_manager_change_invalidates_candidate(change, report):
    user, _ = setup_fields()
    target, assumptions = CustomField.objects.order_by("pk"), declarations()
    candidate = binding.bind_count_query("custom_field", user, target, assumptions)
    assert candidate.candidate is not None, candidate.plan
    upstream = SoftDeleteManager.get_queryset
    def changed_manager(manager):
        query = upstream(manager)
        if manager.model is CustomFieldInstance:
            if change == "remove_delete_filter":
                query.query.where.children.clear()
            else:
                query = query.filter(value_int__isnull=False)
        return query
    with patch.object(SoftDeleteManager, "get_queryset", changed_manager):
        expected = binding.original_for("custom_field", target, user)
        actual, route = binding.execute_count_candidate("custom_field", candidate, user, target, assumptions)
    assert actual == expected and route["route"] == "original", route
    report.append({"kind": "manager_invalidation", "case": change, "status": "passed", "route": route})


@pytest.mark.django_db
def test_candidate_cannot_cross_relation_specs(report):
    user, _ = setup_fields()
    target, assumptions = CustomField.objects.order_by("pk"), declarations()
    candidate = binding.bind_count_query("custom_field", user, target, assumptions)
    tag = Tag.objects.create(name="reuse-tag", owner=user)
    Document.objects.first().tags.add(tag)
    tags = Tag.objects.order_by("pk")
    expected = binding.original(tags, user)
    actual, route = binding.execute_count_candidate("tag", candidate, user, tags, assumptions)
    assert actual == expected and route["route"] == "original", route
    report.append({"kind": "guard", "case": "cross_spec_token", "status": "passed", "route": route})


@pytest.mark.django_db
def test_missing_contract_and_unbound_query(report):
    user, _ = setup_fields()
    target, assumptions = CustomField.objects.order_by("pk"), declarations()
    missing = dict(assumptions)
    del missing["fixed_read_state"]
    denied = binding.bind_count_query("custom_field", user, target, missing)
    assert denied.candidate is None and denied.plan["decision"] == "needs_evidence"
    candidate = binding.bind_count_query("custom_field", user, target, assumptions)
    assert candidate.candidate is not None, candidate.plan
    subset = target.filter(pk=CustomField.objects.order_by("pk").first().pk)
    actual, route = binding.execute_count_candidate("custom_field", candidate, user, subset, assumptions)
    assert actual == binding.original_for("custom_field", subset, user) and route["route"] == "original"
    report.append({"kind": "guard", "case": "missing_contract_and_changed_query", "status": "passed", "route": route})
