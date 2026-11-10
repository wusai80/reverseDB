"""Bounded B0/B2 differential checks and deliberately wrong SQL variants."""
import hashlib
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection

from documents.models import Document, Tag
from tag_counts_b2 import (
    UnsupportedEntry, compile_tag_counts_b2, read_tag_counts_b0, read_tag_counts_b2,
)


@pytest.fixture(scope="module")
def report():
    records = []
    yield records
    result = {"schema_version": 1, "scope": "B2 ordered Tag helper; frozen data, successful execution",
              "records": records, "performance_claim": None}
    Path(os.environ["REVERSEDB_RESULTS"], "tag-b2.json").write_text(
        json.dumps(result, indent=2) + "\n")


def setup_case(tags, documents, seed, density, deleted):
    assert not Tag.objects.exists()
    user = User.objects.create(username="b2-superuser", is_active=True, is_superuser=True)
    other = User.objects.create(username="b2-other")
    # Same names across owners; insertion/name order cannot stand in for PK order.
    target = [Tag.objects.create(name=f"tag-{(tags-i)//2}", owner=user if i % 2 else other)
              for i in range(tags)]
    expected = {t.pk: 0 for t in target}
    links = []
    deleted_ids = []
    rng = random.Random(seed)
    for i in range(documents):
        document = Document.objects.create(
            title=f"doc-{i}", checksum=hashlib.md5(str(i).encode()).hexdigest(),
            mime_type="application/pdf", owner=other if i % 2 else None,
        )
        excluded = deleted == "all" or (deleted == "some" and i % 3 == 0)
        if excluded:
            deleted_ids.append(document.pk)
        for tag in target:
            if rng.random() < density:
                links.append(Document.tags.through(document_id=document.pk, tag_id=tag.pk))
                if not excluded:
                    expected[tag.pk] += 1
    Document.tags.through.objects.bulk_create(links)
    Document.objects.filter(pk__in=deleted_ids).update(
        deleted_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    return user, other, sorted(expected.items()), len(links)


def capture(function):
    queries = []

    def trace(execute, sql, params, many, context):
        assert not many
        queries.append({"sql": sql, "params": list(params or ())})
        return execute(sql, params, many, context)

    with connection.execute_wrapper(trace):
        result = function()
    return result, queries


CASES = [
    ("empty", 0, 0, 42, 0, "none"),
    ("documents_without_tags", 0, 3, 42, 1, "none"),
    ("zero_documents", 4, 0, 42, 0, "none"),
    ("all_deleted", 4, 3, 42, 1, "all"),
    ("no_links", 4, 3, 42, 0, "none"),
    ("mixed_deleted_shared_names", 6, 9, 42, 0.5, "some"),
    ("sparse_42", 25, 60, 42, 0.08, "some"),
    ("dense_43", 30, 80, 43, 0.8, "some"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("name,tags,documents,seed,density,deleted", CASES,
                         ids=[case[0] for case in CASES])
def test_differential(name, tags, documents, seed, density, deleted, report):
    assert connection.vendor == "postgresql" and connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    user, _, expected, edge_count = setup_case(tags, documents, seed, density, deleted)
    # All writes have ended. No restoration between implementations is needed.
    compiled, compilation_queries = capture(lambda: compile_tag_counts_b2(user))
    assert compilation_queries == []
    original, b0_queries = capture(lambda: read_tag_counts_b0(user))
    candidate, b2_queries = capture(lambda: read_tag_counts_b2(user))
    assert original == candidate == expected
    assert len(b0_queries) == 2 and len(b2_queries) == 1
    assert all(type(pk) is int and type(n) is int for pk, n in candidate)
    # The actual aggregate statement is retained byte for byte as a CTE.
    assert f'counts AS ({b0_queries[0]["sql"]})' in compiled[0]
    assert tuple(b0_queries[0]["params"]) == compiled[1]
    assert "CASE WHEN" not in b2_queries[0]["sql"]
    report.append({"kind": "differential", "case": name, "seed": seed,
                   "tags": tags, "documents": documents, "edges": edge_count,
                   "expected": expected, "b0": original, "b2": candidate,
                   "b0_queries": b0_queries, "b2_queries": b2_queries,
                   "status": "passed"})


@pytest.mark.django_db
def test_wrong_variants_are_detected(report):
    user, _, expected, _ = setup_case(4, 3, 42, 0.5, "some")
    # Guarantee both a zero-count row and an excluded link without depending on RNG.
    zero = Tag.objects.create(name="explicit-zero", owner=user)
    deleted = Document.objects.create(title="deleted-only", checksum="f" * 32,
                                      mime_type="application/pdf", owner=user)
    deleted.tags.add(zero)
    Document.objects.filter(pk=deleted.pk).update(
        deleted_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    expected.append((zero.pk, 0))
    original = read_tag_counts_b0(user)
    assert read_tag_counts_b2(user) == original == expected
    sql, params = compile_tag_counts_b2(user)
    variants = {
        "inner_join_drops_zero": (sql.replace("LEFT JOIN counts", "INNER JOIN counts"), params),
        "default_one_changes_zero": (sql.replace('COALESCE(c."c", 0)', 'COALESCE(c."c", 1)'), params),
    }
    # Replace A with all linked IDs, intentionally losing its deletion predicate.
    with patch("tag_counts_b2.permitted_document_ids",
               return_value=Document.tags.through.objects.values_list("document_id", flat=True)):
        variants["wrong_allowed_ids_counts_deleted"] = compile_tag_counts_b2(user)
    for name, (mutant_sql, mutant_params) in variants.items():
        assert mutant_sql != sql
        with connection.cursor() as cursor:
            cursor.execute(mutant_sql, mutant_params)
            wrong = cursor.fetchall()
        assert wrong != original, name
        report.append({"kind": "negative_control", "case": name, "expected": original,
                       "wrong": wrong, "sql": mutant_sql, "params": mutant_params,
                       "status": "detected"})


@pytest.mark.django_db
def test_unsupported_identity_falls_back(report):
    active, ordinary, _, _ = setup_case(3, 4, 42, 0.6, "some")
    inactive = User.objects.create(username="inactive", is_active=False, is_superuser=True)
    for name, user in (("ordinary", ordinary), ("inactive_superuser", inactive), ("no_user", None)):
        with pytest.raises(UnsupportedEntry):
            compile_tag_counts_b2(user)
        expected = read_tag_counts_b0(user)
        actual, queries = capture(lambda: read_tag_counts_b2(user))
        assert expected == actual
        assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
        report.append({"kind": "fallback", "case": name, "expected": expected,
                       "actual": actual, "status": "passed"})
