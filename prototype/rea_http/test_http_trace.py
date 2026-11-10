"""Actual loopback HTTP, synthetic data, unmodified Paperless WSGI application.

Sequential requests only. Outer WSGI instrumentation spans auth and body iteration;
it does not propagate into background tasks, other DB aliases, or worker threads.
"""
import http.client
import json
import os
import threading
import time
import uuid
from urllib.parse import parse_qsl
from datetime import datetime, timezone
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from django.contrib.auth.models import Permission, User
from django.core.wsgi import get_wsgi_application
from django.db import connection
from rest_framework.authtoken.models import Token

from documents.models import Document, Tag
from trace_sql import SQLTrace


class TracedWSGI:
    def __init__(self, app):
        self.app = app
        self.requests = []

    def __call__(self, environ, start_response):
        request_id = uuid.uuid4().hex
        trace = SQLTrace()
        record = {"request_id": request_id, "method": environ["REQUEST_METHOD"],
                  "path": environ["PATH_INFO"], "query_string": environ["QUERY_STRING"]}

        def start(status, headers, exc_info=None):
            record["status"] = int(status.split()[0])
            return start_response(status, headers + [("X-ReverseDB-Trace-ID", request_id)], exc_info)

        def iterate():
            started = time.perf_counter_ns()
            try:
                # Resolve the thread-local connection in the HTTP serving thread.
                with connection.execute_wrapper(trace), trace.during(request_id):
                    response = self.app(environ, start)
                    try:
                        yield from response
                    finally:
                        if hasattr(response, "close"):
                            response.close()
            finally:
                record.update(server_ns_with_trace=time.perf_counter_ns() - started,
                              queries=trace.queries)
                self.requests.append(record)
        return iterate()


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def capture(port, token, scenario, *, path="/api/tags/?page_size=1&ordering=name&is_root=true",
            version=9, method="GET", json_body=None):
    headers = {"Host": f"127.0.0.1:{port}", "Accept": f"application/json; version={version}",
               "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Token {token}"
    request_body = None if json_body is None else json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    if request_body is not None:
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(request_body))})
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    stamp = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter_ns()
    try:
        client.request(method, path, body=request_body, headers=headers)
        sent = time.perf_counter_ns()
        response = client.getresponse()
        received_headers = time.perf_counter_ns()
        body = response.read()
        finished = time.perf_counter_ns()
        response_headers = [{"name": k, "value": v} for k, v in response.getheaders()]
        entry = {
            "startedDateTime": stamp, "time": (finished - started) / 1e6,
            "request": {"method": method, "url": f"http://127.0.0.1:{port}{path}",
                        "httpVersion": "HTTP/1.1", "headers": [
                            {"name": k, "value": v} for k, v in headers.items()
                            if k != "Authorization"], "cookies": [],
                        "queryString": [{"name": k, "value": v} for k, v in
                                        parse_qsl(path.partition("?")[2])],
                        "headersSize": -1, "bodySize": len(request_body) if request_body is not None else 0},
            "response": {"status": response.status, "statusText": response.reason,
                         "httpVersion": "HTTP/1.0" if response.version == 10 else "HTTP/1.1",
                         "headers": response_headers, "cookies": [],
                         "content": {"size": len(body), "mimeType": response.getheader("Content-Type", "").split(";")[0],
                                     "text": body.decode("utf-8")},
                         "redirectURL": "", "headersSize": -1, "bodySize": len(body)},
            "cache": {}, "timings": {"send": (sent-started)/1e6,
                                        "wait": (received_headers-sent)/1e6,
                                        "receive": (finished-received_headers)/1e6},
            "_scenario": scenario, "_redactions": ["request.headers.Authorization"] if token else [],
        }
        if request_body is not None:
            entry["request"]["postData"] = {"mimeType": "application/json", "text": request_body.decode("utf-8")}
        return entry
    finally:
        client.close()


@pytest.mark.django_db(transaction=True)
def test_real_http_to_sql(settings):
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    settings.ALLOWED_HOSTS = ["127.0.0.1"]
    owner = User.objects.create(username="http-owner", is_staff=True, is_superuser=True)
    viewer = User.objects.create(username="http-viewer")
    viewer.user_permissions.add(Permission.objects.get(content_type__app_label="documents", codename="view_tag"))
    tokens = [Token.objects.create(user=u).key for u in (owner, viewer)]
    parent = Tag.objects.create(name="Alpha", owner=owner)
    child = Tag.objects.create(name="Beta", owner=owner, tn_parent=parent)
    Tag.objects.create(name="Zero", owner=owner)
    document = Document.objects.create(title="synthetic-http-1", content="", checksum="1"*32,
                                       mime_type="application/pdf", owner=owner)
    document.tags.add(parent, child)
    app = TracedWSGI(get_wsgi_application())
    server = make_server("127.0.0.1", 0, app, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entries = []
    try:
        for token, label in [(tokens[0], "owner_first"), (tokens[0], "owner_repeat"),
                             (tokens[1], "viewer_without_object_access"), (None, "anonymous")]:
            entries.append(capture(server.server_port, token, label))
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    assert not thread.is_alive()
    output = Path(os.environ["REVERSEDB_RESULTS"])
    har = {"log": {"version": "1.2", "creator": {"name": "ReverseDB http.client recorder", "version": "1"},
                   "comment": "Actual container loopback HTTP on synthetic test data. Authorization omitted. Timings include tracing; not a benchmark.",
                   "entries": entries}}
    (output/"http.har").write_text(json.dumps(har, indent=2))
    (output/"server-trace.json").write_text(json.dumps({"scope": "Sequential WSGI/default DB only; no concurrent writes, background jobs, or other DB aliases.", "requests": app.requests}, indent=2))
    assert len(app.requests) == len(entries) == 4
    ids = [next(h["value"] for h in e["response"]["headers"] if h["name"].lower() == "x-reversedb-trace-id") for e in entries]
    assert len(set(ids)) == 4
    assert set(ids) == {r["request_id"] for r in app.requests}
    for index in (0, 1):
        assert entries[index]["response"]["status"] == 200
        data = json.loads(entries[index]["response"]["content"]["text"])
        assert data["results"][0]["id"] == parent.pk
        assert data["results"][0]["document_count"] == 1
        assert data["results"][0]["children"][0]["id"] == child.pk
    assert entries[2]["response"]["status"] == 200
    assert json.loads(entries[2]["response"]["content"]["text"])["results"] == []
    assert entries[3]["response"]["status"] in (401, 403)
    for rid in ids[:2]:
        queries = next(r["queries"] for r in app.requests if r["request_id"] == rid)
        assert any("GROUP BY" in q["sql"].upper() for q in queries)
        assert all(q["phase"] == rid for q in queries)
