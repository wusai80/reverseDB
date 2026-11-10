"""Handwritten B2 for one frozen-data experiment, not a production QuerySet API.

Only the audited active-superuser, all-Tag, immediate ordered integer-pair
consumer is lowered. Other identities use the original helper. Frozen data,
the pinned schema/runtime and successful execution are experiment preconditions,
not conditions this module can automatically establish.
"""
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Count

from documents.models import Document, Tag
from documents.permissions import (
    annotate_document_count_for_related_queryset,
    permitted_document_ids,
)


class UnsupportedEntry(ValueError):
    pass


def read_tag_counts_b0(user):
    return list(annotate_document_count_for_related_queryset(
        Tag.objects.order_by("pk"), Document.tags.through, "tag_id", user=user,
    ).values_list("pk", "document_count"))


def compile_tag_counts_b2(user):
    if not (connection.vendor == "postgresql" and type(user) is User
            and not user._state.adding and user._state.db == "default"
            and user.is_active and user.is_superuser):
        raise UnsupportedEntry("Only the audited saved active-superuser entry is supported")
    target = Tag.objects.order_by("pk")
    # Keep the original manager, membership predicates and DISTINCT aggregate.
    # Compilation preserves permission parameters without materializing IDs.
    counts = Document.tags.through.objects.filter(
        tag_id__in=target.values("pk"),
        document_id__in=permitted_document_ids(user),
    ).values("tag_id").annotate(c=Count("document_id", distinct=True))
    target_sql, target_params = target.values("pk").query.sql_with_params()
    counts_sql, counts_params = counts.query.sql_with_params()
    return render_tag_counts_sql(target_sql, target_params, counts_sql, counts_params)


def render_tag_counts_sql(target_sql, target_params, counts_sql, counts_params):
    """Shared lowering template; inputs must come from the audited ORM binder."""
    return render_count_sql(target_sql, target_params, counts_sql, counts_params, "tag_id")


def render_count_sql(target_sql, target_params, counts_sql, counts_params, group_key):
    """group_key comes from audited model metadata; escape it as an identifier."""
    group_identifier = '"' + group_key.replace('"', '""') + '"'
    sql = f'''WITH target_rows AS ({target_sql}), counts AS ({counts_sql})
SELECT t."pk", COALESCE(c."c", 0) AS "document_count"
FROM target_rows t LEFT JOIN counts c ON c.{group_identifier} = t."pk"
ORDER BY t."pk" ASC'''
    return sql, (*target_params, *counts_params)


def read_tag_counts_b2(user):
    try:
        sql, params = compile_tag_counts_b2(user)
    except UnsupportedEntry:
        return read_tag_counts_b0(user)
    # Query errors propagate: do not retry on the original path after a failure.
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [(int(pk), int(count)) for pk, count in cursor.fetchall()]
