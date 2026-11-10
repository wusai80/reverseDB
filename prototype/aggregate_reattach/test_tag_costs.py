"""Predeclared local warm-cache cost screen, not a production/API benchmark."""
import hashlib
import json
import os
import random
import statistics
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import django
import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings

from documents.models import Document, Tag
from tag_count_baselines import read_orm_join, read_orm_subquery, read_python_merge
from tag_counts_b2 import read_tag_counts_b0, read_tag_counts_b2
from test_tag_b2 import CASES, capture, setup_case


METHODS = {"B0": read_tag_counts_b0, "ORM_join": read_orm_join,
           "ORM_subquery": read_orm_subquery, "Python_merge": read_python_merge,
           "B2": read_tag_counts_b2}
COST_CASES = [
    ("small", 30, 80, 5, 42),
    ("medium_sparse", 300, 2000, 3, 43),
    ("wide", 1000, 5000, 5, 44),
    ("dense", 300, 3000, 30, 45),
]
BLOCKS, ROUNDS, WARMUPS = 5, 7, 3


@pytest.fixture(scope="module")
def results():
    report = {"schema_version": 1, "scope": "Tag local warm-cache cost screen",
              "correctness": [], "costs": [],
              "protocol": {"blocks": BLOCKS, "rounds_per_block": ROUNDS,
                           "warmups_per_method": WARMUPS, "method_order_seed": 20261103,
                           "method_order": "shuffle all methods independently each round",
                           "timed_boundary": "construct/compile/query/fetch/convert full ordered list",
                           "timed_trace": False, "concurrency": 1,
                           "cache": "warm shared DB/process; new QuerySet each call",
                           "replication": "5 temporal blocks in one DB/process, not independent deployments",
                           "memory": "one separate tracemalloc call per method; Python allocations only",
                           "decision": "screen only; retain all cases; no tuning after results"}}
    yield report
    Path(os.environ["REVERSEDB_RESULTS"], "tag-costs.json").write_text(
        json.dumps(report, indent=2) + "\n")


@pytest.mark.django_db
@pytest.mark.parametrize("name,tags,documents,seed,density,deleted", CASES,
                         ids=[x[0] for x in CASES])
def test_baseline_correctness(name, tags, documents, seed, density, deleted, results):
    user, _, expected, _ = setup_case(tags, documents, seed, density, deleted)
    for method, function in METHODS.items():
        actual = function(user)
        assert actual == expected
        assert all(type(k) is int and type(v) is int for k, v in actual)
    results["correctness"].append({"case": name, "methods": list(METHODS), "status": "passed"})


def bulk_fixture(tag_count, document_count, fanout, seed):
    assert not Tag.objects.exists()
    user = User.objects.create(username="cost-superuser", is_active=True, is_superuser=True)
    other = User.objects.create(username="cost-other")
    tags = Tag.objects.bulk_create([
        Tag(name=f"cost-{i//2}", owner=user if i % 2 else other) for i in range(tag_count)
    ])
    instant = datetime(2026, 11, 3, tzinfo=timezone.utc)
    docs = Document.objects.bulk_create([
        Document(title=f"cost-{i}", checksum=hashlib.md5(str(i).encode()).hexdigest(),
                 mime_type="application/pdf", owner=other if i % 2 else None,
                 deleted_at=instant if i % 4 == 0 else None)
        for i in range(document_count)
    ])
    expected = {t.pk: 0 for t in tags}
    links = []
    rng = random.Random(seed)
    for i, doc in enumerate(docs):
        for tag in rng.sample(tags, fanout):
            links.append(Document.tags.through(document_id=doc.pk, tag_id=tag.pk))
            if i % 4:
                expected[tag.pk] += 1
    Document.tags.through.objects.bulk_create(links, batch_size=2000)
    # Statistics are common to every implementation; no index or planner tuning.
    with connection.cursor() as cursor:
        for model in (Tag, Document, Document.tags.through):
            cursor.execute("ANALYZE " + connection.ops.quote_name(model._meta.db_table))
    return user, sorted(expected.items()), len(links)


@pytest.mark.django_db
@pytest.mark.parametrize("name,tags,documents,fanout,seed", COST_CASES,
                         ids=[x[0] for x in COST_CASES])
def test_cost_screen(name, tags, documents, fanout, seed, results):
    assert connection.vendor == "postgresql" and connection.settings_dict["HOST"] == "db"
    assert connection.settings_dict["NAME"].startswith("test_")
    user, expected, edge_count = bulk_fixture(tags, documents, fanout, seed)
    record = {"case": name, "tags": tags, "documents": documents, "fanout": fanout,
              "edges": edge_count, "seed": seed, "expected_sha256": hashlib.sha256(
                  json.dumps(expected).encode()).hexdigest(), "methods": {}, "order": []}
    with override_settings(DEBUG=False):
        assert not connection.queries_logged and not connection.execute_wrappers
        # Trace only for correctness/SQL inspection, outside timed samples.
        for method, function in METHODS.items():
            actual, queries = capture(lambda: function(user))
            assert actual == expected
            record["methods"][method] = {"queries": queries, "samples_ns": [],
                                           "block_medians_ns": []}
        for _ in range(WARMUPS):
            for function in METHODS.values():
                assert function(user) == expected
        rng = random.Random(20261103)
        for block in range(BLOCKS):
            block_samples = {m: [] for m in METHODS}
            for round_number in range(ROUNDS):
                order = list(METHODS)
                rng.shuffle(order)
                record["order"].append({"block": block, "round": round_number, "methods": order})
                for method in order:
                    assert not connection.execute_wrappers and not connection.queries_logged
                    started = time.perf_counter_ns()
                    actual = METHODS[method](user)
                    elapsed = time.perf_counter_ns() - started
                    assert actual == expected  # result checking is outside the timed interval
                    block_samples[method].append(elapsed)
                    record["methods"][method]["samples_ns"].append(elapsed)
            for method in METHODS:
                record["methods"][method]["block_medians_ns"].append(
                    statistics.median(block_samples[method]))
        # Diagnostics run after timing; their instrumentation cannot enter samples.
        for method, function in METHODS.items():
            tracemalloc.start()
            try:
                actual = function(user)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            assert actual == expected
            item = record["methods"][method]
            item["python_peak_bytes_diagnostic"] = peak
            item["median_ns"] = statistics.median(item["samples_ns"])
            item["explain"] = []
            with connection.cursor() as cursor:
                for query in item["queries"]:
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query["sql"], query["params"])
                    item["explain"].append(cursor.fetchone()[0])
        with connection.cursor() as cursor:
            settings = {}
            for setting in ("server_version", "transaction_isolation", "work_mem", "shared_buffers",
                            "jit", "max_parallel_workers_per_gather", "random_page_cost"):
                cursor.execute("SHOW " + setting)  # fixed allowlist above
                settings[setting] = cursor.fetchone()[0]
        record["environment"] = {"django": django.get_version(), "postgres_settings": settings,
                                   "queries_logged": connection.queries_logged,
                                   "execute_wrappers": len(connection.execute_wrappers)}
    record["status"] = "passed"
    results["costs"].append(record)
