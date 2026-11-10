#!/usr/bin/env python3
"""Join observed HTTP records to instrumented SQL; reject ambiguous evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def correlate(entries, requests):
    by_id = {}
    for request in requests:
        rid = request["request_id"]
        if not rid or rid in by_id:
            raise ValueError("Missing or duplicate server request ID")
        by_id[rid] = request
    seen = set()
    rows = []
    for ordinal, entry in enumerate(entries):
        ids = [h["value"] for h in entry["response"]["headers"]
               if h["name"].lower() == "x-reversedb-trace-id"]
        if len(ids) != 1 or not ids[0] or ids[0] in seen or ids[0] not in by_id:
            raise ValueError("Missing, duplicate, or unmatched HTTP request ID")
        rid = ids[0]
        seen.add(rid)
        server = by_id[rid]
        url = urlsplit(entry["request"]["url"])
        if (server["method"], server["path"], server["query_string"], server["status"]) != (
            entry["request"]["method"], url.path, url.query, entry["response"]["status"]
        ):
            raise ValueError("Request ID matches but HTTP metadata differs")
        queries = server["queries"]
        if any(q["phase"] != rid for q in queries):
            raise ValueError("SQL query belongs to another request")
        counts = Counter(q["sql"] for q in queries)
        groups = [q for q in queries if "GROUP BY" in q["sql"].upper()]
        rows.append({"ordinal": ordinal, "request_id": rid,
                     "scenario": entry.get("_scenario"), "http_status": server["status"],
                     "sql_count": len(queries), "group_by_count": len(groups),
                     "repeated_sql_templates": [
                         {"sha256": hashlib.sha256(sql.encode()).hexdigest(), "count": count}
                         for sql, count in counts.items() if count > 1],
                     "group_by_call_paths": [
                         [frame["function"] for frame in q["stack"]
                          if "/documents/" in frame["file"] or "/rest_framework/" in frame["file"]]
                         for q in groups]})
    if seen != set(by_id):
        raise ValueError("Server requests are absent from HTTP evidence")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    root = args.run
    har_bytes = (root/"http.har").read_bytes()
    har = json.loads(har_bytes)
    rea = json.loads((root/"rea-result.json").read_text())
    trace = json.loads((root/"server-trace.json").read_text())
    if rea["subject"]["digest"]["sha256"] != hashlib.sha256(har_bytes).hexdigest():
        raise ValueError("REA analyzed different HAR bytes")
    normalized = rea["normalized_result"]
    entries = [r["reported"] for r in normalized["records"]]
    if normalized["total_records"] != len(har["log"]["entries"]) or entries != har["log"]["entries"]:
        raise ValueError("REA did not preserve the complete recorded HTTP entries")
    rows = correlate(entries, trace["requests"])
    report = {"scope": "Observed sequential requests and default DB only. Correlation is instrumentation, not inferred causality or proof of full query coverage.",
              "rea_evidence_id": rea["evidence_id"], "har_sha256_verified": True,
              "rea_exact_record_roundtrip": True, "requests": rows,
              "limitations": ["SQL parameter values are not captured: matching SQL templates alone does not prove equivalent queries.",
                              "HTTP evidence does not independently identify ORM permissions or transaction semantics.",
                              "No background-task, multi-database, concurrency, or optimization validation."]}
    (root/"correlation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
