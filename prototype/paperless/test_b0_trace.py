"""Small B0 smoke fixture, not the full differential/benchmark matrix."""
import json
import os
import time
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from django.db import connection
from rest_framework.test import APIClient

from documents.models import Document, Tag
from documents.permissions import annotate_document_count_for_related_queryset
from trace_sql import SQLTrace


@pytest.mark.django_db
def test_original_helper_and_tag_api():
    # This harness must never fall back silently to a host DB or SQLite.
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    user = User.objects.create(username="reversedb-owner", is_staff=True, is_superuser=True)
    parent = Tag.objects.create(name="Alpha", owner=user)
    child = Tag.objects.create(name="Beta", owner=user, tn_parent=parent)
    empty = Tag.objects.create(name="Zero", owner=user)
    document = Document.objects.create(
        title="synthetic-1", content="", checksum="0" * 32,
        mime_type="application/pdf", owner=user,
    )
    document.tags.add(parent, child)
    expected = [[parent.pk, 1], [child.pk, 1], [empty.pk, 0]]
    trace = SQLTrace()
    with connection.execute_wrapper(trace):
        with trace.during("helper_build"):
            queryset = annotate_document_count_for_related_queryset(
                Tag.objects.order_by("pk"), Document.tags.through, "tag_id", user=user,
            )
        with trace.during("helper_evaluate"):
            mapping = [list(row) for row in queryset.values_list("pk", "document_count")]
    assert mapping == expected
    assert any("GROUP BY" in q["sql"].upper() for q in trace.queries if q["phase"] == "helper_build")
    assert any("CASE" in q["sql"].upper() for q in trace.queries if q["phase"] == "helper_evaluate")

    client = APIClient()
    client.force_authenticate(user=user)
    responses = []
    for version in (9, 10):
        for cache_state in ("django_cache_cleared", "warm_repeat"):
            if cache_state == "django_cache_cleared":
                for cache in caches.all():
                    cache.clear()
            phase = f"api_v{version}_{cache_state}"
            with connection.execute_wrapper(trace), trace.during(phase):
                started = time.perf_counter_ns()
                response = client.get(
                    "/api/tags/", {"page_size": 1, "ordering": "name", "is_root": "true"},
                    HTTP_ACCEPT=f"application/json; version={version}",
                )
                elapsed = time.perf_counter_ns() - started
            assert response.status_code == 200
            data = response.json()
            assert data["results"][0]["id"] == parent.pk
            assert data["results"][0]["document_count"] == 1
            assert data["results"][0]["children"][0]["id"] == child.pk
            assert data["results"][0]["children"][0]["document_count"] == 1
            responses.append({"phase": phase, "client_request_ns_with_trace": elapsed, "response": data})
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        postgres_version = cursor.fetchone()[0]
        cursor.execute("SHOW transaction_isolation")
        isolation = cursor.fetchone()[0]
    result = {
        "schema_version": 1, "implementation": "B0", "data": "synthetic-smoke-v1",
        "postgres_version": postgres_version, "transaction_isolation": isolation,
        "scope": "Unmodified upstream helper/API; pytest transaction, no concurrent writes; APIClient, not network HTTP. Execute times exclude fetch and include wrapper overhead. Cache labels describe Django cache clearing only, not verified cold tree/permission caches.",
        "mapping": mapping, "responses": responses, "queries": trace.queries,
    }
    (Path(os.environ["REVERSEDB_RESULTS"]) / "b0-trace.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
