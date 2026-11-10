"""Reproduce a MANUAL source binding and small semantic counterexamples.

Anchors locate inspected code; matching text is not a semantic analyzer.
No application execution, SQL lowering, live catalog check, or deployment.
"""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUNDTRIP = "evidence/tpcc-roundtrip/20261103-9954559e225c"
AUDIT = "evidence/code-audit-fixes/20261104-13435f1b4859"
JAVA = ROUNDTRIP + "/bundle/sources/candidate/reversedb/tpcc/"
PAPERLESS = "sources/paperless-ngx/src/"
sources = {}
anchors = {}


def read(path):
    data = (ROOT / path).read_bytes()
    sources[path] = hashlib.sha256(data).hexdigest()
    return data.decode()


def anchor(name, path, fragment):
    source = read(path)
    if source.count(fragment) != 1:
        raise ValueError(f"Anchor must occur exactly once: {name}")
    line = source[:source.index(fragment)].count("\n") + 1
    anchors[name] = dict(path=path, line=line, fragment=fragment)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    anchor("scalar_read", JAVA + "StockLevelService.java", '"SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=?"')
    anchor("scalar_decode", JAVA + "StockLevelService.java", "var4 = var6.getInt(1);")
    anchor("scalar_missing", JAVA + "StockLevelService.java", 'throw new SQLException("Missing district");')
    anchor("scalar_compute", JAVA + "StockLevelService.java", "var4 - 20,")
    anchor("scalar_consumer", JAVA + "CandidateApp.java", 'new JSONObject().put("low_stock_count"')
    anchor("scalar_encode", JAVA + "Sql.java", "var3.setObject(var4 + 1, var2[var4]);")
    anchor("transaction", "app/tpcc/src/reversedb/tpcc/AppRuntime.java", "setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE)")
    anchor("ddl_scalar", "work/tpcc/source/src/main/resources/benchmarks/tpcc/ddl-postgres.sql", "d_next_o_id int            NOT NULL")
    anchor("map_materialize", PAPERLESS + "documents/permissions.py", 'counts_by_pk = {row[related_object_field]: row["c"] for row in counts}')
    anchor("map_encode", PAPERLESS + "documents/permissions.py", "*(When(pk=pk, then=Value(count)) for pk, count in counts_by_pk.items())")
    anchor("audited_consumer", "prototype/aggregate_reattach/tag_counts_b2.py", "def read_tag_counts_b0(user):")
    anchor("runtime_binder", "prototype/aggregate_reattach/tag_query_binding.py", "def bind_count_query(")
    anchor("tag_page", PAPERLESS + "documents/views.py", "page = self.paginate_queryset(queryset)\n        serializer = self.get_serializer(page, many=True)")
    anchor("tag_descendants", PAPERLESS + "documents/views.py", "descendant_pks = {pk for tag in all_tags for pk in tag.get_descendants_pks()}")
    anchor("tag_child_consumer", PAPERLESS + "documents/serialisers.py", "serializer = TagSerializer(\n            children,")
    anchor("pagination", PAPERLESS + "paperless/views.py", 'response_data.append(("all", self.get_all_result_ids()))')
    anchor("model_consumer", "prototype/app_db_questions/finite_model.py", "window = target[offset:offset + limit]")

    manifest = json.loads(read(ROUNDTRIP + "/manifest.json"))
    for path, digest in list(sources.items()):
        if path.startswith(JAVA):
            require(manifest[path[len(ROUNDTRIP) + 1:]] == digest, "Roundtrip source mismatch")
    audit_manifest = json.loads(read(AUDIT + "/manifest.json"))
    history_path = AUDIT + "/tag-query-binding.json"
    history = json.loads(read(history_path))
    require(audit_manifest["files"]["tag-query-binding.json"] == sources[history_path], "Historical report mismatch")
    for path in ("prototype/aggregate_reattach/tag_query_binding.py", "prototype/aggregate_reattach/tag_counts_b2.py"):
        archived = "inputs/" + Path(path).name
        read(AUDIT + "/" + archived)
        require(audit_manifest["files"][archived] == sources[AUDIT + "/" + archived] == sources[path],
                "Audited local implementation changed")
    historical = [r for r in history["records"] if r["kind"] == "differential"]
    require(len(historical) == 8, "Unexpected historical differential case set")
    for record in historical:
        require(record["facts"]["source_sha256"] == sources[PAPERLESS + "documents/permissions.py"], "Helper source changed")
        require(record["facts"]["consumer"] == "immediate-pk-ascending-int-pairs/v1", "Unexpected consumer")

    # Model Java signed int wrapping, without pretending to execute JDBC/Postgres.
    lower = -(2**31)
    next_id = lower + 1
    exact_subtraction = next_id - 20
    java_subtraction = (exact_subtraction + 2**31) % 2**32 - 2**31
    require(java_subtraction == 2147483629, "Unexpected int32 witness")
    original_count = int(java_subtraction <= lower < next_id)
    widened_count = int(exact_subtraction <= lower < next_id)
    require((original_count, widened_count) == (0, 1), "Widened-bound mutation survived")
    counterexamples = [
        dict(id="P1_scalar_integer_bridge", scope="arithmetic model; not database execution",
             input=dict(d_next_o_id=next_id, ol_o_id=lower, s_i_id=1, s_quantity=5, threshold=10),
             original=dict(java_lower_bound=java_subtraction, count=original_count),
             naive_widening=dict(lower_bound=exact_subtraction, count=widened_count),
             direct_int32_sql_obligation="exact subtraction is outside int32; not interchangeable with Java wrap"),
        dict(id="P2_nested_consumer", scope="manual source-derived response model; not API execution",
             proposed_mutation="Apply the root-page key set to BOTH the root and child aggregate producers",
             input=dict(page_keys=[0], child_of={"1": 0}, effective_links=[[1, 7]]),
             original=dict(id=0, document_count=0, children=[dict(id=1, document_count=1)]),
             page_keys_only=dict(id=0, document_count=0, children=[dict(id=1, document_count=0)])),
    ]
    require(counterexamples[1]["original"] != counterexamples[1]["page_keys_only"], "Nested-consumer mutation survived")

    bindings = [
        dict(id="P1_stocklevel", decision="unknown", action="keep_original",
             mapping="Q1 -> getInt -> Java int subtract -> Integer/setObject -> Q2 -> getInt -> JSON",
             established=["closed scalar use", "missing district branch", "distinct half-open window", "local transaction source"],
             refuted=["unconditional replacement by SQL int subtraction or mathematical bigint subtraction"],
             unknown=["lowering preserving the typed bridge", "live schema/driver/context", "bound fault-domain contract"],
             next_obligation="Preserve int32 subtraction semantics or establish a program invariant; retain missing-row branch and result decoder"),
        dict(id="P1_tag_map", decision="unknown", action="keep_original_until_live_rebind",
             mapping="effective grouped Count DISTINCT -> Python dict -> zero/CASE -> lazy QuerySet -> ordered integer pairs",
             historical_decision="accept_given_declared_context", historical_cases=len(historical),
             historical_isolation=sorted({r["facts"]["context"][3] for r in historical}),
             established=["helper source and audited implementation identity", "local map closure", "historical runtime facts"],
             declared_in_history=["fixed read state", "immediate consumer", "successful execution", "no observable read effects", "pinned runtime"],
             unknown=["current live binding", "arbitrary API consumers"],
             next_obligation="Reuse existing live binder only for its exact contract; do not promote historical facts to live authorization"),
        dict(id="P2_existing_full_pairs", decision="accept", scope="abstract demand rule for the established full-pairs consumer only",
             demand="all target keys", reduction="none", action="no_new_optimization"),
        dict(id="P2_explicit_page_fixture", decision="accept", scope="finite mathematical model only",
             demand="distinct keys of predetermined target slice", runtime_decision="unknown",
             assumptions=["all count consumers explicit", "total exact integer operations", "fixed inputs and occurrence order"],
             next_obligation="Bind a database fixture with explicit base-field order/page and full typed observation"),
        dict(id="P2_tag_api_page_only", decision="reject", scope="one root-page key set applied to both aggregate producers, not all possible API optimization",
             reasons=["recursive children expose counts outside root page", "document_count ordering supported", "Lower(name) does not establish a total order"],
             runtime_decision="keep_original", next_obligation="Use the finite consumer grammar first; do not silently add tree closure to this stage"),
        dict(id="P2_tag_initial_aggregate_only", decision="unknown", action="keep_original",
             distinction="Restricting the initial aggregate alone need not affect the separate child aggregate",
             unknown=["per-producer value and row demand", "request-specific ordering/filter dependencies", "full API type/effect/order binding"],
             next_obligation="Keep root-row discovery separate from count-value demand; a subtree witness does not refute this narrower transformation"),
    ]
    read(str(Path(__file__).relative_to(ROOT)))
    return dict(status="review_completed", method="manual bindings with reproducible source anchors and model witnesses",
                sources=sources, anchors=anchors, bindings=bindings, counterexamples=counterexamples,
                limitations=["No automatic semantic inference from anchors", "No live DB metadata or API run",
                             "DDL and local runtime source are design evidence, not live facts",
                             "Historical snapshot assumption is declared, not implied by READ COMMITTED"])


if __name__ == "__main__":
    report = run()
    output = ROOT / "evidence/app-db-bindings/20261104/binding-review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(status=report["status"], source_files=len(report["sources"]),
                         anchors=len(report["anchors"]), bindings=len(report["bindings"]),
                         counterexamples=len(report["counterexamples"]))))
