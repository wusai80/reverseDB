#!/usr/bin/env python3
"""Rebuild immutable source evidence and syntactic Python call-site inventory.

This does not execute upstream code or resolve dynamic dispatch / generated SQL.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from checkout_sources import ROOT, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the evidence snapshot")
    args = parser.parse_args()
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    repos = {r["name"]: r for r in lock["repositories"]}
    for name, repo in repos.items():
        verify(ROOT / "sources" / name, repo)
    spec = json.loads((ROOT / "regions.json").read_text())
    records = []
    for region in spec["regions"]:
        repo = repos[region["repository"]]
        rel = Path(region["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Source paths must stay within the checkout")
        source = ROOT / "sources" / repo["name"] / rel
        raw = source.read_bytes()
        content = raw.decode("utf-8")
        lines = content.splitlines()
        start, end = region["lines"]
        if not 1 <= start <= end <= len(lines):
            raise ValueError(f"Bad source range: {region['id']}")
        excerpt = "\n".join(lines[start - 1:end]) + "\n"
        if region["anchor"] not in excerpt:
            raise ValueError(f"Missing anchor: {region['id']}")
        record = dict(region)
        record.update({
            "commit": repo["commit"],
            "source_url": f"{repo['url'][:-4]}/blob/{repo['commit']}/{rel.as_posix()}#L{start}-L{end}",
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
            "excerpt": excerpt,
            "evidence_level": "static_source",
        })
        if source.suffix == ".py":
            tree = ast.parse(content, filename=str(rel))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and start <= n.lineno <= end]
            record["syntactic_calls"] = [
                {"line": n.lineno, "column": n.col_offset, "callee": ast.unparse(n.func)}
                for n in sorted(calls, key=lambda n: (n.lineno, n.col_offset))
            ]
        records.append(record)
    result = {
        "schema_version": 1,
        "scope": "Manually selected regions; source-order call sites are NOT runtime order, call graph, effect proof or SQL trace.",
        "sources_lock_sha256": hashlib.sha256((ROOT / "sources.lock.json").read_bytes()).hexdigest(),
        "regions": records,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    dest = ROOT / "evidence" / "static-evidence.json"
    if args.write:
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        print(f"Wrote {len(records)} source regions: {dest}")
    elif dest.read_text(encoding="utf-8") != rendered:
        raise RuntimeError("Evidence changed; inspect differences before regenerating")
    else:
        print(f"PASS: {len(records)} regions, anchors, SHA256 hashes and call sites match pinned clean checkouts")


if __name__ == "__main__":
    main()
