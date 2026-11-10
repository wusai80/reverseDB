"""Experimental B1, scoped to this pinned TagViewSet's list GET only.

Normal fixed-data response equivalence is the target, not equivalence under
query failure, concurrent changes, custom backends or arbitrary get_queryset effects.
"""
from contextlib import contextmanager
from unittest.mock import patch

from documents.permissions import PaperlessObjectPermissions
from documents.views import TagViewSet


class TagListMetadataPermissions(PaperlessObjectPermissions):
    def _queryset(self, view):
        if (type(view) is TagViewSet and getattr(view, "action", None) == "list"
                and getattr(getattr(view, "request", None), "method", None) == "GET"):
            # DRF 3.18.3 has_permission consumes only queryset.model here.
            # Auth, perms_map, user.has_perms, filtering and object permissions
            # retain their original implementations.
            return view.queryset.all()
        return super()._queryset(view)


@contextmanager
def candidate(enabled=True):
    if not enabled:
        yield
        return
    original = TagViewSet.permission_classes
    assert PaperlessObjectPermissions in original
    replacement = tuple(TagListMetadataPermissions if p is PaperlessObjectPermissions else p
                        for p in original)
    with patch.object(TagViewSet, "permission_classes", replacement):
        yield
