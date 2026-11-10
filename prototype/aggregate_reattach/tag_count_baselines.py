"""Conventional alternatives for the already-bound integer-pair consumer only."""
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from documents.models import Document, Tag
from documents.permissions import permitted_document_ids


def read_orm_join(user):
    return list(Tag.objects.order_by("pk").values("pk").annotate(
        document_count=Count("documents", distinct=True,
                             filter=Q(documents__pk__in=permitted_document_ids(user))),
    ).values_list("pk", "document_count"))


def read_orm_subquery(user):
    counts = Document.tags.through.objects.filter(
        tag_id=OuterRef("pk"), document_id__in=permitted_document_ids(user),
    ).order_by().values("tag_id").annotate(c=Count("document_id", distinct=True)).values("c")
    return list(Tag.objects.order_by("pk").annotate(
        document_count=Coalesce(Subquery(counts, output_field=IntegerField()), Value(0)),
    ).values_list("pk", "document_count"))


def read_python_merge(user):
    targets = Tag.objects.order_by("pk")
    counts = Document.tags.through.objects.filter(
        tag_id__in=targets.values("pk"), document_id__in=permitted_document_ids(user),
    ).values("tag_id").annotate(c=Count("document_id", distinct=True))
    mapping = {row["tag_id"]: row["c"] for row in counts}
    return [(pk, mapping.get(pk, 0)) for pk in targets.values_list("pk", flat=True)]
