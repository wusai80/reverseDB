#!/usr/bin/env python3
"""Run Tag binding or B2 checks in an isolated frozen B0 environment."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import paperless_baseline as base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--b2", action="store_true", help="run bounded B0/B2 differential checks")
    selection.add_argument("--costs", action="store_true", help="verify conventional baselines and run local cost screen")
    selection.add_argument("--bind-query", action="store_true", help="run live query binder and B2 regression checks")
    selection.add_argument("--reuse", action="store_true", help="run CustomField reuse and full Tag regression")
    selection.add_argument("--callsites", action="store_true", help="bind two extracted candidates and run existing regression")
    selection.add_argument("--guard-costs", action="store_true", help="fixed duplicate guard before/after screen and regression")
    selection.add_argument("--parse-costs", action="store_true", help="exact-source syntax cache checks, fixed costs and regression")
    selection.add_argument("--catalog-costs", action="store_true", help="batch live catalog reads, DDL guards and fixed costs")
    selection.add_argument("--audit-fixes", action="store_true", help="audit regressions and existing correctness guards; no performance run")
    args = parser.parse_args()
    suite = "reuse" if args.reuse else ("query-binding" if args.bind_query else ("costs" if args.costs else ("b2" if args.b2 else "binding")))
    if args.callsites:
        suite = "callsites"
    if args.guard_costs:
        suite = "guard-costs"
    if args.parse_costs:
        suite = "parse-costs"
    if args.catalog_costs or args.audit_fixes:
        suite = "catalog-costs"
    if args.audit_fixes:
        suite = "audit-fixes"
    prefix = base.require_ready()
    lock = base.load_lock()
    built = base.load_build(prefix, lock)
    project = f"reversedb-{suite}-" + uuid.uuid4().hex[:12]
    container = project + "-app"
    destination = base.ROOT / f"work/tag-{suite}" / project
    destination.mkdir(parents=True)
    harness = base.ROOT / "prototype/aggregate_reattach"
    source = harness / ("test_tag_costs.py" if args.costs else ("test_tag_b2.py" if args.b2 else "test_tag_binding.py"))
    sources = [source, harness / "tag_counts_b2.py"] if args.b2 else [source]
    if args.costs:
        sources = [source, harness / "tag_counts_b2.py", harness / "tag_count_baselines.py", harness / "test_tag_b2.py"]
    if args.bind_query or args.reuse or args.callsites or args.guard_costs or args.parse_costs or args.catalog_costs or args.audit_fixes:
        source = harness / ("test_binding_reuse.py" if args.reuse else "test_tag_query_binding.py")
        sources = [source, *[harness / name for name in (
            "tag_query_binding.py", "tag_binding_profile.json", "transform_contract.py",
            "custom_field_binding_profile.json", "tag_counts_b2.py", "test_tag_b2.py")]]
        if args.reuse or args.callsites or args.guard_costs or args.parse_costs or args.catalog_costs or args.audit_fixes:
            sources.append(harness / "test_tag_query_binding.py")
        if args.callsites or args.guard_costs or args.parse_costs or args.catalog_costs or args.audit_fixes:
            source = harness / "test_callsite_binding.py"
            sources = list(dict.fromkeys([*sources, source, harness / "test_binding_reuse.py",
                                          harness / "callsite_binding.py", harness / "callsite_extraction.py"]))
        if args.guard_costs or args.parse_costs or args.catalog_costs or args.audit_fixes:
            source = harness / "test_callsite_guard_costs.py"
            sources.append(source)
        if args.parse_costs or args.catalog_costs or args.audit_fixes:
            source = harness / "test_callsite_parse_costs.py"
            sources.append(source)
        if args.catalog_costs or args.audit_fixes:
            source = harness / "test_callsite_catalog_costs.py"
            sources.append(source)
        if args.audit_fixes:
            source = harness / "test_audit_fixes.py"
            sources.append(source)
    inputs = destination / "inputs"
    inputs.mkdir()
    for item in sources:
        shutil.copy2(item, inputs / item.name)
    if args.guard_costs or args.parse_costs or args.catalog_costs or args.audit_fixes:
        archived = base.ROOT / "evidence/count-callsite-binding/20261104-432d1491b8e1/inputs/callsite_binding.py"
        expected = "6c542df2dbb9388fe3416f21d78a7e0e89594a7790af43a54c322c188e847754"
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == expected
        snapshot = inputs / "callsite_binding_before.py"
        shutil.copy2(archived, snapshot)
        sources.append(snapshot)
    if args.parse_costs or args.catalog_costs or args.audit_fixes:
        archived = base.ROOT / "evidence/callsite-guard-costs/20261104-bfefcd212527/inputs/callsite_binding.py"
        expected = "b583f9ecc44c155e99ec61d7ea20692f5c22c82fa416da3a2918307f6a3b74b2"
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == expected
        snapshot = inputs / "callsite_binding_uncached.py"
        shutil.copy2(archived, snapshot)
        sources.append(snapshot)
    if args.catalog_costs or args.audit_fixes:
        prior = base.ROOT / "evidence/callsite-parse-costs/20261104-bf2603ce0935"
        manifest = json.loads((prior / "manifest.json").read_text())
        provenance = {}
        for archived_name, name in (("callsite_binding.py", "callsite_catalog_before.py"),
                                    ("tag_query_binding.py", "tag_query_catalog_before.py")):
            origin = prior / "inputs" / archived_name
            expected = manifest["files"]["inputs/" + archived_name]
            assert hashlib.sha256(origin.read_bytes()).hexdigest() == expected
            snapshot = inputs / name
            shutil.copy2(origin, snapshot)
            sources.append(snapshot)
            provenance[name] = {"origin": str(origin.relative_to(base.ROOT)), "sha256": expected}
        baseline_record = inputs / "catalog-baseline.json"
        base.write_json(baseline_record, provenance)
        sources.append(baseline_record)
    shutil.copy2(__file__, inputs / Path(__file__).name)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "RDB_"))}
    env.update(RDB_POSTGRES_IMAGE=lock["images"]["postgres"]["resolved"],
               RDB_BROKER_IMAGE=lock["images"]["broker"]["resolved"],
               RDB_APP_IMAGE=built["app"]["id"])
    compose = prefix + ["compose", "--env-file", "/dev/null", "-p", project,
                        "-f", str(base.HARNESS / "compose.yaml")]
    record = {"project": project, "started_at": base.now(), "status": "running",
              "images_lock": lock, "build_lock": built, "steps": []}

    def run(name, command, timeout=120):
        record["steps"].append({"name": name, "command": command})
        base.write_json(destination / "run.json", record)
        base.run_logged(command, destination / (name + ".log"), env=env, timeout=timeout)

    try:
        run("config", compose + ["config"])
        run("services", compose + ["up", "-d", "db", "broker"])
        base.wait_services(prefix, project, destination / "health.json")
        run("app", compose + ["run", "--name", container, "--no-deps", "-d",
                              "--entrypoint", "sleep", "app", "infinity"])
        for item in sources:
            run("copy-" + item.stem, prefix + ["cp", str(inputs / item.name), container + ":/harness/" + item.name])
        test_files = ["/harness/" + source.name]
        if args.parse_costs or args.guard_costs or args.catalog_costs or args.audit_fixes:
            test_files += ["/harness/test_callsite_binding.py", "/harness/test_binding_reuse.py",
                           "/harness/test_tag_b2.py", "/harness/test_tag_query_binding.py"]
            if args.parse_costs or args.catalog_costs or args.audit_fixes:
                test_files += ["/harness/test_callsite_guard_costs.py::test_changed_runtime_still_checked_once",
                               "/harness/test_callsite_guard_costs.py::test_business_fault_does_not_retry"]
            if args.catalog_costs or args.audit_fixes:
                test_files += ["/harness/test_callsite_parse_costs.py::test_syntax_reuse_boundaries",
                               "/harness/test_callsite_parse_costs.py::test_exact_text_key_and_bounded_immutable_result",
                               "/harness/test_callsite_parse_costs.py::test_parse_errors_are_not_cached"]
            if args.audit_fixes:
                test_files += ["/harness/test_callsite_catalog_costs.py::test_real_ddl_invalidates_binding"]
        elif args.callsites:
            test_files += ["/harness/test_binding_reuse.py", "/harness/test_tag_b2.py", "/harness/test_tag_query_binding.py"]
        elif args.reuse:
            test_files += ["/harness/test_tag_b2.py", "/harness/test_tag_query_binding.py"]
        elif args.bind_query:
            test_files += ["/harness/test_tag_b2.py"]
        run("probe", prefix + ["exec", container, "python", "-m", "pytest",
                               "-c", "/app/pyproject.toml", "-o", "addopts=", "-p", "no:cacheprovider",
                               "--randomly-seed=42", f"--junitxml=/results/{suite}.xml"] + test_files, timeout=900)
        run("artifacts", prefix + ["cp", container + ":/results/.", str(destination)])
        report = json.loads((destination / f"tag-{suite}.json").read_text())
        if args.audit_fixes:
            assert report["scope"] == "Regression fixes for code audit P1 and two P2 findings"
            assert len(report["records"]) == 48
            assert all(r["status"] == "passed" for r in report["records"])
            for filename, count in (("tag-catalog-costs.json", 6), ("tag-parse-costs.json", 8),
                                    ("tag-guard-costs.json", 14)):
                checks = json.loads((destination / filename).read_text())
                assert len(checks["guards"]) == count and not checks["costs"]
                assert all(r["status"] == "passed" for r in checks["guards"])
            for filename, count in (("tag-callsites.json", 32), ("tag-reuse.json", 14),
                                    ("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.catalog_costs:
            assert report["scope"] == "Batch fresh catalog metadata without admission caching"
            assert len(report["guards"]) == 6 and len(report["costs"]) == 4
            assert all(r["status"] == "passed" for r in report["guards"] + report["costs"])
            for filename, count in (("tag-parse-costs.json", 8), ("tag-guard-costs.json", 14)):
                checks = json.loads((destination / filename).read_text())
                assert len(checks["guards"]) == count and not checks["costs"]
                assert all(r["status"] == "passed" for r in checks["guards"])
            for filename, count in (("tag-callsites.json", 32), ("tag-reuse.json", 14),
                                    ("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.parse_costs:
            assert report["scope"] == "Exact-source syntax reuse without admission caching"
            assert len(report["guards"]) == 8 and len(report["costs"]) == 4
            assert all(r["status"] == "passed" for r in report["guards"] + report["costs"])
            guard_report = json.loads((destination / "tag-guard-costs.json").read_text())
            assert len(guard_report["guards"]) == 14 and not guard_report["costs"]
            assert all(r["status"] == "passed" for r in guard_report["guards"])
            for filename, count in (("tag-callsites.json", 32), ("tag-reuse.json", 14),
                                    ("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.guard_costs:
            assert report["scope"] == "Single duplicate runtime guard removal"
            assert len(report["guards"]) == 14 and len(report["costs"]) == 4
            assert all(r["status"] == "passed" for r in report["guards"] + report["costs"])
            for filename, count in (("tag-callsites.json", 32), ("tag-reuse.json", 14),
                                    ("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.callsites:
            assert report["scope"].startswith("Two extracted configurations")
            assert len(report["records"]) == 32
            assert all(r["status"] == "passed" for r in report["records"])
            for filename, count in (("tag-reuse.json", 14), ("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.reuse:
            assert report["scope"].startswith("Two audited relation specs")
            assert len(report["records"]) == 14
            assert all(r["status"] in ("passed", "detected") for r in report["records"])
            for filename, count in (("tag-query-binding.json", 23), ("tag-b2.json", 14)):
                regression = json.loads((destination / filename).read_text())
                assert len(regression["records"]) == count
                assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.bind_query:
            assert report["scope"].startswith("Live Tag query binding")
            assert len(report["records"]) == 23
            assert all(r["status"] == "passed" for r in report["records"])
            regression = json.loads((destination / "tag-b2.json").read_text())
            assert len(regression["records"]) == 14
            assert all(r["status"] in ("passed", "detected") for r in regression["records"])
        elif args.costs:
            assert report["scope"] == "Tag local warm-cache cost screen"
            assert len(report["correctness"]) == 8 and len(report["costs"]) == 4
            assert all(r["status"] == "passed" for r in report["correctness"] + report["costs"])
        elif args.b2:
            assert report["scope"].startswith("B2 ordered Tag helper")
            assert len(report["records"]) == 14
            assert all(r["status"] in ("passed", "detected") for r in report["records"])
        else:
            assert report["scope"].startswith("Original ordered Tag helper")
        record["status"] = "passed"
    except BaseException as exc:
        record.update(status="failed", error=str(exc))
        try:
            run("partial-artifacts", prefix + ["cp", container + ":/results/.", str(destination)])
        except Exception:
            pass
    finally:
        cleanup_errors = []
        for name, command in [("app-cleanup", prefix + ["rm", "--force", container]),
                              ("cleanup", compose + ["down", "--volumes", "--timeout", "10"])]:
            try:
                run(name, command, timeout=60)
            except Exception as exc:
                if name == "app-cleanup" and "No such container" in (destination / (name + ".log")).read_text():
                    continue
                cleanup_errors.append(str(exc))
        record.update(cleanup_errors=cleanup_errors, finished_at=base.now())
        base.write_json(destination / "run.json", record)
        print("Evidence:", destination, flush=True)
    return 0 if record["status"] == "passed" and not cleanup_errors else 1


if __name__ == "__main__":
    sys.exit(main())
