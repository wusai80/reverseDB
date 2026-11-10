"""Batch live column/RLS reads; retain identical facts and DDL invalidation."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from django.db import connection

import callsite_binding as after
import callsite_catalog_before as before
import tag_query_binding as runtime
import tag_query_catalog_before as old_runtime
from test_callsite_binding import extracted, setup
from test_callsite_guard_costs import CASES, BLOCKS, ROUNDS, WARMUPS, run_cost_screen
from test_tag_b2 import capture

# The archived adapter uses its archived binder, not the current optimized reader.
before.runtime = old_runtime
BASE = Path(__file__).with_name("catalog-baseline.json")


@pytest.fixture(scope="session")
def results():
    provenance = json.loads(BASE.read_text())
    for filename, item in provenance.items():
        assert hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest() == item["sha256"]
    result = {"scope": "Batch fresh catalog metadata without admission caching", "guards": [], "costs": [],
              "baseline": provenance,
              "protocol": {"blocks": BLOCKS, "rounds": ROUNDS, "warmups": WARMUPS,
                           "order_seed": 20261104, "timed_trace": False,
                           "boundary": "fresh QuerySet + candidate bind + guarded execution + full result",
                           "cache": "warm exact-source syntax only; catalog/schema read every bind",
                           "replication": "one process/database; temporal blocks, not independent deployments"}}
    yield result
    Path(os.environ["REVERSEDB_RESULTS"], "tag-catalog-costs.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["tag", "custom_field"])
@pytest.mark.parametrize("change", ["link_nullable", "rls", "extra_index"])
def test_real_ddl_invalidates_binding(key, change, results):
    user, target, expected, declarations = setup(key)
    # Fixture inserts may leave deferred FK trigger events; settle them before
    # issuing DDL in this test's rollback-isolated transaction.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    spec = runtime.SPECS[key]
    (old_facts, old_queries) = capture(lambda: old_runtime._schema_and_context(spec))
    (new_facts, new_queries) = capture(lambda: runtime._schema_and_context(spec))
    assert old_facts == new_facts and len(old_queries) == 13 and len(new_queries) == 8
    candidate = after.bind_callsite(extracted(key), user, target, declarations)
    assert candidate.fingerprint is not None, candidate.plan
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if change == "link_nullable":
            cursor.execute("ALTER TABLE " + quote(spec.link_model._meta.db_table) +
                           " ALTER COLUMN " + quote(spec.link_model._meta.get_field(spec.relation_field).column) +
                           " DROP NOT NULL")
        elif change == "rls":
            cursor.execute("ALTER TABLE " + quote(spec.target_model._meta.db_table) + " ENABLE ROW LEVEL SECURITY")
        else:
            cursor.execute("CREATE INDEX " + quote("rdb_catalog_probe_" + key) +
                           " ON " + quote(spec.target_model._meta.db_table) + " (id)")
    if change == "extra_index":
        old_changed = old_runtime._schema_and_context(spec)
        new_changed = runtime._schema_and_context(spec)
        assert old_changed == new_changed and new_changed != new_facts
    else:
        for reader in (old_runtime._schema_and_context, runtime._schema_and_context):
            with pytest.raises((runtime.UnboundEntry, old_runtime.UnboundEntry)):
                reader(spec)
    (rows, route), queries = capture(lambda: after.execute_callsite(
        candidate, user, target, declarations, fallback=lambda: runtime.original_for(key, target, user)))
    assert rows == runtime.original_for(key, target, user) == expected and route["route"] == "original"
    assert not any(q["sql"].startswith("WITH target_rows") for q in queries)
    results["guards"].append({"relation": key, "case": change, "status": "passed",
                              "original_facts": new_facts, "route": route,
                              "old_reader_queries": old_queries, "new_reader_queries": new_queries})


@pytest.mark.django_db
@pytest.mark.parametrize("key,name,args", CASES, ids=[k+":"+n for k,n,_ in CASES])
def test_fixed_catalog_cost_screen(key, name, args, results):
    run_cost_screen(key, name, args, results, baseline=before, bind_counts=(2, 2), query_counts=(27, 17))
    spec = runtime.SPECS[key]
    assert old_runtime._schema_and_context(spec) == runtime._schema_and_context(spec)
    record = results["costs"][-1]
    record["full_catalog_facts_equal"] = True
