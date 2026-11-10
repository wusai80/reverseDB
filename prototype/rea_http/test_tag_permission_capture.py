"""Observe identity and grant transitions in a disposable, unmodified app."""
import json
import os
import threading
import time
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.wsgi import get_wsgi_application
from django.db import connection
from rest_framework.authtoken.models import Token

from documents.models import Tag
from paperless_testing.permissions import grant_global
from test_http_trace import QuietHandler, capture, make_server


@pytest.mark.django_db(transaction=True)
def test_tag_permission_transitions(settings):
    assert connection.vendor == "postgresql" and connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    settings.ALLOWED_HOSTS = ["127.0.0.1"]
    users = {role: User.objects.create(username="permission-probe-" + role)
             for role in ("owner", "peer", "stranger", "no_model")}
    for role in ("owner", "peer", "stranger"):
        grant_global(users[role], "view_tag", "add_tag", "change_tag", "delete_tag")
    tokens = {role: Token.objects.create(user=user).key for role, user in users.items()}
    tokens["anonymous"] = None
    tags = [Tag.objects.create(name=name, owner=users["owner"])
            for name in ("Permission Amber", "Permission Cobalt")]
    names = {t.pk: t.name for t in tags}
    entries = {role: [] for role in tokens}
    timeline, checks = [], []
    server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(role, phase, action, method, path, expected, payload=None):
        time.sleep(0.02)
        entry = capture(server.server_port, tokens[role], f"{role}:{phase}:{action}",
                        path=path, version=10, method=method, json_body=payload)
        ordinal = len(entries[role])
        entries[role].append(entry)
        timeline.append({"sequence": len(timeline), "role": role, "phase": phase,
                         "action": action, "actor_record_ordinal": ordinal,
                         "method": method, "path": path, "status": entry["response"]["status"]})
        assert entry["response"]["status"] == expected, timeline[-1]
        text = entry["response"]["content"]["text"]
        return json.loads(text) if text else None

    try:
        for phase in ("private", "view_shared", "edit_shared", "revoked"):
            visible = phase in ("view_shared", "edit_shared")
            editable = phase == "edit_shared"
            if phase != "private":
                # Only the ordinary owner changes permissions, through real HTTP.
                permissions = {"view": {"users": [users["peer"].pk, users["no_model"].pk] if visible else [], "groups": []},
                               "change": {"users": [users["peer"].pk] if editable else [], "groups": []}}
                for tag in tags:
                    request("owner", phase, "set_permissions", "PATCH", f"/api/tags/{tag.pk}/", 200,
                            {"set_permissions": permissions})
                    request("owner", phase, "grant_readback", "GET", f"/api/tags/{tag.pk}/", 200)
                    request("owner", phase, "grant_list_readback", "GET", "/api/tags/?page_size=100&ordering=id", 200)
            for role in tokens:
                accessible = role == "owner" or (role == "peer" and visible)
                gate_status = 401 if role == "anonymous" else 403 if role == "no_model" else 200
                listed = request(role, phase, "list", "GET", "/api/tags/?page_size=100&ordering=id", gate_status)
                ids = [t.pk for t in tags] if accessible else []
                if gate_status == 200:
                    assert [t["id"] for t in listed["results"]] == ids
                    assert listed["count"] == len(ids)
                for tag in tags:
                    status = gate_status if gate_status != 200 else 200 if accessible else 404
                    detail = request(role, phase, "detail", "GET", f"/api/tags/{tag.pk}/", status)
                    if status == 200:
                        assert detail["name"] == names[tag.pk]
                        assert detail["user_can_change"] == (role == "owner" or editable)
                checks.append({"phase": phase, "role": role, "list_status": gate_status,
                               "visible_ids": ids if gate_status == 200 else None,
                               "detail_status": status,
                               "user_can_change": role == "owner" or editable if accessible else None})
            for tag in tags:
                expected = 200 if editable else 403 if visible else 404
                renamed = f"Peer edited {tag.pk}"
                request("peer", phase, "rename", "PATCH", f"/api/tags/{tag.pk}/", expected, {"name": renamed})
                if editable:
                    names[tag.pk] = renamed
                    peer_detail = request("peer", phase, "rename_readback", "GET", f"/api/tags/{tag.pk}/", 200)
                    peer_list = request("peer", phase, "rename_list_readback", "GET", "/api/tags/?page_size=100&ordering=id", 200)
                    assert peer_detail["name"] == renamed
                    assert next(t["name"] for t in peer_list["results"] if t["id"] == tag.pk) == renamed
                # Owner reads the same shared object; rejected writes must leave it intact.
                detail = request("owner", phase, "verify_peer_write", "GET", f"/api/tags/{tag.pk}/", 200)
                assert detail["name"] == names[tag.pk]
                if editable:
                    request("peer", phase, "attempt_reshare", "PATCH", f"/api/tags/{tag.pk}/", 403,
                            {"set_permissions": {"view": {"users": [users["stranger"].pk]}}})
                    request("stranger", phase, "verify_reshare_denied", "GET", f"/api/tags/{tag.pk}/", 404)
                    request("peer", phase, "attempt_delete", "DELETE", f"/api/tags/{tag.pk}/", 403)
                    request("owner", phase, "verify_delete_denied", "GET", f"/api/tags/{tag.pk}/", 200)
        assert dict(Tag.objects.values_list("pk", "name")) == names
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        output = Path(os.environ["REVERSEDB_RESULTS"])
        for role, records in entries.items():
            (output/f"permissions-{role}.har").write_text(json.dumps({"log": {
                "version": "1.2", "creator": {"name": "ReverseDB permission recorder", "version": "1"},
                "comment": f"Actual API v10 HTTP, synthetic disposable DB, actor {role} only. Credentials excluded. Other actors may change shared state; see permissions.json timeline. No inferred authorization model.",
                "entries": records}}, indent=2))
        (output/"permissions.json").write_text(json.dumps({
            "scope": "Two flat owned tags; model permissions, direct user sharing, editing, re-sharing refusal, delete refusal, revocation. No groups or ownerless objects.",
            "actors": {role: user.pk for role, user in users.items()},
            "tag_ids": [t.pk for t in tags], "timeline": timeline, "checks": checks,
            "server_stopped": not thread.is_alive()}, indent=2))
    assert not thread.is_alive()
    assert len(checks) == 20
