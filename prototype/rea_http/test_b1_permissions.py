"""B0/B1 differential experiment, plus a deliberate failure-semantics counterexample."""
import inspect
import json
import os
import threading
from pathlib import Path

import pytest
from django.contrib.auth.models import Group, User
from django.core.cache import caches
from django.core.wsgi import get_wsgi_application
from django.db import DatabaseError, connection
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from b1_permissions import candidate
from documents.models import Document, Tag
from paperless_testing.permissions import grant_global, grant_object
from test_http_trace import TracedWSGI, QuietHandler, capture, make_server


def save(name, value):
    (Path(os.environ["REVERSEDB_RESULTS"])/name).write_text(json.dumps(value, indent=2))


def request_id(entry):
    return next(h["value"] for h in entry["response"]["headers"]
                if h["name"].lower() == "x-reversedb-trace-id")


@pytest.mark.django_db(transaction=True)
def test_b1_http_matrix(settings):
    assert connection.vendor == "postgresql" and connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    settings.ALLOWED_HOSTS = ["127.0.0.1"]
    roles = ["super", "owner", "direct", "group", "overlap", "none", "tag_only", "no_model", "inactive"]
    users = {role: User.objects.create(username="b1-"+role, is_superuser=role=="super",
                                       is_active=role!="inactive") for role in roles}
    for role, user in users.items():
        if role != "no_model":
            grant_global(user, "view_tag")
    stranger = User.objects.create(username="stranger")
    parent = Tag.objects.create(name="Alpha", owner=users["owner"])
    child = Tag.objects.create(name="Beta", owner=users["owner"], tn_parent=parent)
    hidden = Tag.objects.create(name="Hidden", owner=stranger, tn_parent=parent)
    zero = Tag.objects.create(name="Zero", owner=users["owner"])
    public = Tag.objects.create(name="Public", owner=None)
    first = Document.objects.create(title="shared", content="", checksum="2"*32,
                                    mime_type="application/pdf", owner=users["owner"])
    first.tags.add(parent, child)
    secret = Document.objects.create(title="secret", content="", checksum="3"*32,
                                     mime_type="application/pdf", owner=stranger)
    secret.tags.add(parent, hidden)
    unowned = Document.objects.create(title="public", content="", checksum="4"*32,
                                      mime_type="application/pdf", owner=None)
    unowned.tags.add(public)
    group = Group.objects.create(name="readers")
    users["group"].groups.add(group)
    users["overlap"].groups.add(group)
    for grantee in [group, users["direct"], users["overlap"], users["tag_only"], users["no_model"]]:
        grant_object(grantee, parent, "view_tag")
        grant_object(grantee, child, "view_tag")
        if grantee != users["tag_only"]:
            grant_object(grantee, first, "view_document")
    tokens = {role: Token.objects.create(user=user).key for role, user in users.items()}
    tokens["anonymous"] = None
    specs = [
        (9, "GET", "/api/tags/?page_size=1&ordering=name&is_root=true", "root_page1"),
        (10, "GET", "/api/tags/?page_size=1&page=2&ordering=-document_count", "count_page2"),
        (9, "GET", "/api/tags/?page_size=25&ordering=name", "full_v9"),
        (10, "GET", "/api/tags/?page_size=25&ordering=-document_count", "full_v10"),
    ]
    # Control paths outside the optimization scope must retain their behavior.
    controls = [("owner", 9, "GET", f"/api/tags/{parent.pk}/", "detail_owner"),
                ("none", 9, "GET", f"/api/tags/{parent.pk}/", "detail_hidden"),
                ("owner", 9, "HEAD", "/api/tags/", "head"),
                ("owner", 9, "OPTIONS", "/api/tags/", "options"),
                ("none", 9, "POST", "/api/tags/", "post_denied")]
    scenarios = [(role, version, method, path, name) for role in tokens for version, method, path, name in specs] + controls
    app = TracedWSGI(get_wsgi_application())
    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entries, pairs = [], []
    try:
        for role, version, method, path, name in scenarios:
            pair = []
            for enabled in (False, True):
                for cache in caches.all():
                    cache.clear()
                with candidate(enabled):
                    entry = capture(server.server_port, tokens[role],
                                    f"{role}:{name}:B{int(enabled)}", path=path,
                                    version=version, method=method)
                pair.append(entry)
                entries.append(entry)
            pairs.append((role, name, method, pair))
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    assert not thread.is_alive()
    save("b1-http.har", {"log": {"version": "1.2", "creator": {"name": "ReverseDB differential HTTP recorder", "version": "1"},
                                 "comment": "Actual loopback HTTP, synthetic data, alternating B0/B1 with cleared Django cache. Authorization omitted. Not a benchmark.", "entries": entries}})
    save("b1-server-trace.json", {"requests": app.requests})
    by_id = {r["request_id"]: r for r in app.requests}
    results = []
    for role, name, method, pair in pairs:
        before, after = pair
        assert before["response"]["status"] == after["response"]["status"], (role, name)
        # Exact bytes, including sequence ordering and pagination URLs.
        assert before["response"]["content"]["text"] == after["response"]["content"]["text"], (role, name)
        bq, aq = [by_id[request_id(e)]["queries"] for e in pair]
        bg, ag = [sum("GROUP BY" in q["sql"].upper() for q in qs) for qs in (bq, aq)]
        targeted = name in {s[3] for s in specs} and role not in ("inactive", "anonymous")
        assert len(bq)-len(aq) == (1 if targeted else 0), (role, name, len(bq), len(aq))
        assert bg-ag == (1 if targeted else 0), (role, name, bg, ag)
        if targeted:
            assert not any("GROUP BY" in q["sql"].upper() and
                           any(f["function"]=="has_permission" for f in q["stack"]) for q in aq)
        if name in ("full_v9", "full_v10"):
            status = after["response"]["status"]
            if role in ("inactive", "anonymous"):
                assert status == 401
            elif role == "no_model":
                assert status == 403
            else:
                assert status == 200
                data = json.loads(after["response"]["content"]["text"])
                counts = {t["id"]: t["document_count"] for t in data["results"]}
                if role == "none":
                    assert counts == {public.pk: 1}
                else:
                    expected_parent = 2 if role=="super" else 0 if role=="tag_only" else 1
                    assert counts[parent.pk] == expected_parent
                    if role != "super":
                        assert hidden.pk not in counts
                    if role in ("super", "owner"):
                        assert counts[zero.pk] == 0
        results.append({"role": role, "case": name, "status": after["response"]["status"],
                        "b0_request_id": request_id(before), "b1_request_id": request_id(after),
                        "b0_sql": len(bq), "b1_sql": len(aq), "b0_group_by": bg, "b1_group_by": ag,
                        "response_bytes_equal": True})
    save("b1-results.json", {"pairs": results, "scope": "Fixed synthetic data, no query faults or concurrent writes; targeted TagViewSet list GET only."})
    (Path(os.environ["REVERSEDB_RESULTS"])/"b1-drf-source.txt").write_text(inspect.getsource(DjangoModelPermissions))


@pytest.mark.django_db
def test_failure_semantics_counterexample(settings):
    settings.DEBUG = False
    user = User.objects.create(username="denied-no-model-permission")
    owner = User.objects.create(username="fault-owner")
    tag = Tag.objects.create(name="Fault-tag", owner=owner)
    doc = Document.objects.create(title="fault", content="", checksum="5"*32,
                                  mime_type="application/pdf", owner=owner)
    doc.tags.add(tag)
    # Give object access, but deliberately omit the required model permission.
    grant_object(user, tag, "view_tag")
    grant_object(user, doc, "view_document")
    results = []
    for enabled in (False, True):
        client = APIClient()
        client.raise_request_exception = False
        client.force_authenticate(User.objects.get(pk=user.pk))
        faults = []

        def fail_count(execute, sql, params, many, context):
            if "GROUP BY" in str(sql).upper():
                faults.append("count-query-failure")
                raise DatabaseError("synthetic count query fault")
            return execute(sql, params, many, context)

        with candidate(enabled), connection.execute_wrapper(fail_count):
            response = client.get("/api/tags/")
        results.append({"implementation": "B1" if enabled else "B0",
                        "status": response.status_code, "injected_faults": len(faults)})
    save("b1-fault-counterexample.json", {"kind": "Deliberately injected failure, not an observed production outage", "results": results})
    assert results == [{"implementation": "B0", "status": 500, "injected_faults": 1},
                       {"implementation": "B1", "status": 403, "injected_faults": 0}]
