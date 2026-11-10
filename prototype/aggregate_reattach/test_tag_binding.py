"""Bind the original Tag helper to the v0 contract; no optimized implementation."""
import inspect
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import django
import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Case, IntegerField, Value, When
from django.db.models.expressions import BaseExpression

from documents.models import Document, Tag
from documents.permissions import (
    annotate_document_count_for_related_queryset,
    permitted_document_ids,
)


def compiled(queryset):
    sql, params = queryset.query.sql_with_params()
    return {"sql": sql, "params": list(params)}


def execute_description(queryset):
    query = compiled(queryset)
    with connection.cursor() as cursor:
        cursor.execute(query["sql"], query["params"])
        columns = [{"name": c.name, "type_oid": c.type_code} for c in cursor.description]
        rows = cursor.fetchall()
    return {**query, "columns": columns, "rows": rows}


@pytest.mark.django_db
def test_original_tag_helper_binding():
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    # Fresh isolated test database, synthetic rows only. All writes precede reads.
    assert not Tag.objects.exists()
    owner = User.objects.create(username="binding-owner", is_superuser=True, is_active=True)
    other = User.objects.create(username="binding-other")
    first = Tag.objects.create(name="Same", owner=owner)
    second = Tag.objects.create(name="Same", owner=other)
    zero = Tag.objects.create(name="Zero", owner=owner)
    visible = Document.objects.create(title="binding-visible", checksum="a" * 32,
                                     mime_type="application/pdf", owner=owner)
    deleted = Document.objects.create(title="binding-deleted", checksum="b" * 32,
                                     mime_type="application/pdf", owner=owner)
    visible.tags.add(first, second)
    deleted.tags.add(first)
    Document.objects.filter(pk=deleted.pk).update(
        deleted_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    links = Document.tags.through
    queries = []
    phase = "helper_build"

    def trace(execute, sql, params, many, context):
        assert not many
        queries.append({"phase": phase, "sql": sql, "params": list(params or ()),
                        "param_types": [type(p).__name__ for p in params or ()]})
        return execute(sql, params, many, context)

    with connection.execute_wrapper(trace):
        annotated = annotate_document_count_for_related_queryset(
            Tag.objects.order_by("pk"), links, "tag_id", user=owner)
        phase = "helper_evaluate"
        selected = annotated.values_list("pk", "document_count")
        rows = list(selected)
    assert rows == [(first.pk, 1), (second.pk, 1), (zero.pk, 0)]
    build = [q for q in queries if q["phase"] == "helper_build"]
    evaluate = [q for q in queries if q["phase"] == "helper_evaluate"]
    assert len(build) == len(evaluate) == 1
    assert "COUNT(DISTINCT" in build[0]["sql"] and "GROUP BY 1" in build[0]["sql"]
    assert "CASE" in evaluate[0]["sql"] and "ORDER BY 1 ASC" in evaluate[0]["sql"]

    manager_query = compiled(links.objects.all())
    assert not links.objects.all().query.where.children
    assert links._meta.auto_created is Document
    fields = {}
    for name in ("tag", "document"):
        field = links._meta.get_field(name)
        assert not field.null
        fields[name] = {"class": type(field).__name__, "null": field.null,
                        "db_type": field.db_type(connection),
                        "target_type": field.target_field.get_internal_type()}

    # Original CASE construction with synthetic counts: no large fixture required.
    # Probe both its DB result type and the actual COUNT/COALESCE result type.
    type_probes = []
    for n in (0, 1, 32767, 32768, 2147483647, 2147483648, 9223372036854775807):
        case = Case(When(pk=first.pk, then=Value(n)), default=Value(0),
                    output_field=IntegerField())
        probe = Tag.objects.filter(pk=first.pk).annotate(document_count=case)
        result = execute_description(probe.values_list("pk", "document_count"))
        assert result["rows"] == [(first.pk, n)]
        orm = list(probe.values_list("pk", "document_count"))
        assert orm == result["rows"] and type(orm[0][1]) is int
        type_probes.append({"synthetic_count": n, **result})
    empty = annotate_document_count_for_related_queryset(
        Tag.objects.none(), links, "tag_id", user=owner)
    assert list(empty.values_list("pk", "document_count")) == []
    # Nonempty T with empty G exercises the Value(0) branch, without mutations.
    only_zero = annotate_document_count_for_related_queryset(
        Tag.objects.filter(pk=zero.pk).order_by("pk"), links, "tag_id", user=owner)
    zero_result = execute_description(only_zero.values_list("pk", "document_count"))
    assert zero_result["rows"] == [(zero.pk, 0)] and "CASE" not in zero_result["sql"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_typeof(COUNT(DISTINCT d))::text, "
                       "pg_typeof(COALESCE(COUNT(DISTINCT d), 0))::text "
                       "FROM (VALUES (1), (1)) AS v(d)")
        aggregate_types = cursor.fetchone()
        cursor.execute("SHOW server_version")
        version = cursor.fetchone()[0]
        cursor.execute("SHOW transaction_isolation")
        isolation = cursor.fetchone()[0]
        cursor.execute("SELECT oid, typname FROM pg_type WHERE oid IN (20, 21, 23, 1700)")
        oid_names = dict(cursor.fetchall())
        constraints = connection.introspection.get_constraints(cursor, links._meta.db_table)
        cursor.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                       "WHERE oid IN (%s::regclass, %s::regclass, %s::regclass)",
                       [Tag._meta.db_table, Document._meta.db_table, links._meta.db_table])
        rls = cursor.fetchall()
    assert len(rls) == 3 and all(not row[1] and not row[2] for row in rls)
    assert any(c["unique"] and set(c["columns"]) == {"document_id", "tag_id"}
               for c in constraints.values())
    assert aggregate_types == ("bigint", "bigint")
    raw_result = execute_description(selected.all())
    assert raw_result["rows"] == rows
    conversion_sources = {}
    for name, function in {
        "postgres_integer_parameter_adapter": connection.ops.adapt_integerfield_value,
        "integer_field_db_prep": IntegerField.get_db_prep_value,
        "integer_field_result_converters": IntegerField.get_db_converters,
        "expression_result_converters": BaseExpression.get_db_converters,
        "expression_convert_value": BaseExpression.convert_value.func,
    }.items():
        file = Path(inspect.getsourcefile(function))
        conversion_sources[name] = {"source": inspect.getsource(function), "file": str(file),
                                    "file_sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    report = {
        "schema_version": 1, "scope": "Original ordered Tag helper only; no B2 or API replacement",
        "django_version": django.get_version(), "postgres_version": version,
        "transaction_isolation": isolation,
        "environment_assumption": "Fresh isolated DB; setup writes finish before reads; no other writers",
        "entry": "annotate_document_count_for_related_queryset(Tag.objects.order_by('pk'), "
                 "Document.tags.through, 'tag_id', user=active_superuser).values_list('pk','document_count')",
        "fixture": {"tags": [first.pk, second.pk, zero.pk], "allowed_document": visible.pk,
                    "excluded_deleted_document": deleted.pk, "duplicate_names": [first.pk, second.pk]},
        "K": compiled(Tag.objects.order_by("pk").values("pk")),
        "A": compiled(permitted_document_ids(owner)), "L": manager_query,
        "manager": {"class": type(links.objects).__module__ + "." + type(links.objects).__name__,
                    "get_queryset_source": inspect.getsource(type(links.objects).get_queryset),
                    "auto_created": "Document", "unique_together": links._meta.unique_together,
                    "fields": fields},
        "original_queries": queries, "original_result": raw_result,
        "empty_map_result": zero_result, "case_type_probes": type_probes,
        "aggregate_types": aggregate_types, "oid_names": oid_names,
        "conversion_sources": conversion_sources, "through_constraints": constraints,
        "rls": rls,
        "annotation_field": annotated.query.annotations["document_count"].output_field.get_internal_type(),
        "python_count_types": [type(row[1]).__name__ for row in rows],
        "consumer": "Immediate full values_list evaluation in pk order, no count arithmetic or pagination",
        "unproven": ["API ordering", "concurrent writes", "query faults", "arbitrary QuerySets", "B2 lowering"],
    }
    destination = Path(os.environ["REVERSEDB_RESULTS"])
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "tag-binding.json").write_text(json.dumps(report, indent=2) + "\n")
