"""Capture successful flat-tag CRUD from a disposable real Paperless instance."""
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
def test_capture_tag_crud(settings):
    assert connection.vendor == "postgresql" and connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    settings.ALLOWED_HOSTS = ["127.0.0.1"]
    user = User.objects.create(username="rea-crud-owner")
    grant_global(user, "view_tag", "add_tag", "change_tag", "delete_tag")
    token = Token.objects.create(user=user).key
    seeds = [Tag.objects.create(name=name, owner=user) for name in ("Anchor Alpha", "Anchor Beta")]
    # No SQL instrumentation or candidate optimization is installed in this phase.
    server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entries, observations = [], []
    list_path = "/api/tags/?page_size=100&ordering=id"

    def request(label, method, path, expected, payload=None):
        # REA 6.8 truncates starts to milliseconds when computing overlap.
        # Real spacing avoids marking sequential sub-ms gaps as overlapping.
        time.sleep(0.02)
        entry = capture(server.server_port, token, label, path=path, version=10,
                        method=method, json_body=payload)
        entries.append(entry)
        assert entry["response"]["status"] == expected, (label, entry["response"])
        text = entry["response"]["content"]["text"]
        return json.loads(text) if text else None

    def read_list(label):
        data = request(label, "GET", list_path, 200)
        assert data["next"] is None and data["previous"] is None
        return data

    created = []
    try:
        for i in range(2):
            assert read_list(f"initial-list-{i}")["count"] == 2
            for tag in seeds:
                request(f"initial-detail-{i}-{tag.pk}", "GET", f"/api/tags/{tag.pk}/", 200)
        for i, name in enumerate(("Research Amber", "Research Cobalt")):
            data = request(f"create-{i}", "POST", "/api/tags/", 201,
                           {"name": name, "owner": user.pk, "color": "#a6cee3"})
            pk = data["id"]  # Use the actual server-assigned ID, never a guessed ID.
            created.append(pk)
            detail = request(f"created-detail-{i}", "GET", f"/api/tags/{pk}/", 200)
            assert detail["name"] == name and detail["owner"] == user.pk
            listed = read_list(f"created-list-{i}")
            assert pk in {t["id"] for t in listed["results"]}
            observations.append({"action": "create", "id": pk, "name": name})
        time.sleep(1.1)  # Separate create/edit timestamps for evidence-based clock inference.
        for i, pk in enumerate(created):
            name = ("Edited Amber", "Edited Cobalt")[i]
            request(f"update-{i}", "PATCH", f"/api/tags/{pk}/", 200, {"name": name})
            detail = request(f"updated-detail-{i}", "GET", f"/api/tags/{pk}/", 200)
            assert detail["name"] == name
            listed = read_list(f"updated-list-{i}")
            assert next(t["name"] for t in listed["results"] if t["id"] == pk) == name
            observations.append({"action": "update", "id": pk, "name": name})
        for i, pk in enumerate(created):
            request(f"delete-{i}", "DELETE", f"/api/tags/{pk}/", 204)
            request(f"deleted-detail-{i}", "GET", f"/api/tags/{pk}/", 404)
            listed = read_list(f"deleted-list-{i}")
            assert pk not in {t["id"] for t in listed["results"]}
            observations.append({"action": "delete", "id": pk})
        assert set(Tag.objects.values_list("pk", flat=True)) == {t.pk for t in seeds}
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        output = Path(os.environ["REVERSEDB_RESULTS"])
        (output/"crud.har").write_text(json.dumps({"log": {"version": "1.2",
            "creator": {"name": "ReverseDB actual HTTP CRUD recorder", "version": "1"},
            "comment": "Actual unmodified Paperless; disposable synthetic data; one ordinary owner/API v10; Authorization omitted. Flat tags, no documents, no permission changes. Real 20ms gaps separate requests for REA interval analysis.",
            "entries": entries}}, indent=2))
        (output/"crud-observations.json").write_text(json.dumps({"actions": observations,
            "records": len(entries), "owner_id": user.pk, "seed_ids": [t.pk for t in seeds],
            "scope": "Flat tag create, rename, delete; no SQL tracing or optimization."}, indent=2))
    assert not thread.is_alive()
    assert len(entries) == 24 and len(observations) == 6
