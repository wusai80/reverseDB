#!/usr/bin/env python3
"""Static control-feature development regression; no scores are retrained."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "prototype/module_pruning"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    out = ROOT / "evidence/module-control" / ("20261104-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status="running", production_authorized=False, refits=0,
                  new_database_runs=0, new_performance_runs=0, new_label_comparisons=0)
    frozen_files = {}
    def freeze(path):
        destination = out / "inputs" / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        frozen_files[str(destination.relative_to(out))] = digest(destination)
        return destination
    try:
        protocol = json.loads((BASE/"control_feature_protocol.json").read_text())
        old = ROOT / protocol["source_archive"]
        manifest = json.loads((old/"manifest.json").read_text())
        old_files = {}
        for name in ["corpus.json", "results.json", "sealed_predictions.json", "report.json",
                     "inputs/prototype/module_pruning/external_validation_protocol.json"]:
            assert digest(old/name) == manifest[name], name
            old_files[name] = freeze(old/name)
        freeze(old/"manifest.json")
        original_hashes = {}
        for name in ["effect_features.py", "calibrate_risk.py", "risk_model.json"]:
            expected = manifest["inputs/prototype/module_pruning/" + name]
            assert digest(BASE/name) == expected, name
            original_hashes[name] = expected
            freeze(BASE/name)
        for name in ["control_features.py", "control_feature_protocol.json", "test_control_features.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json", frozen_files)
        frozen = out / "inputs/prototype/module_pruning"
        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(frozen), "-p", "test_control_features.py", "-v"]
        completed = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        (out/"tests.log").write_text(completed.stdout)
        report["test_command"] = command
        if completed.returncode:
            raise RuntimeError("Control feature tests failed; see tests.log")
        report["unit_tests"] = int(re.search(r"Ran (\d+) tests", completed.stdout).group(1))
        sys.path.insert(0, str(frozen))
        from control_features import extract_control, FEATURES
        corpus = json.loads(old_files["corpus.json"].read_text())
        domains = json.loads(old_files["inputs/prototype/module_pruning/external_validation_protocol.json"].read_text())["input_domains"]
        facts = []
        for case in corpus:
            try:
                result = extract_control(case["source"], domains)
            except ValueError as error:
                result = dict(status="unsupported", reason=str(error), features={k: None for k in FEATURES},
                              deletion_authorized=False, effect_summary_complete=False)
            facts.append(dict(id=case["id"], source_sha256=hashlib.sha256(case["source"].encode()).hexdigest(), **result))
        save(out/"sealed_control_facts.json", facts)
        sealed_hash = digest(out/"sealed_control_facts.json")
        # Already-known labels are joined only after source-derived facts seal.
        rows = json.loads(old_files["results.json"].read_text())
        previous = {r["id"]: r for r in rows}
        enriched = []
        for item in facts:
            old_row = previous[item["id"]]
            old_features = old_row["prediction"].get("features")
            vector = None if old_features is None else {**old_features, **item["features"]}
            enriched.append(dict(id=item["id"], old_features=old_features, control_features=item["features"],
                                 features=vector, feature_vector_complete=vector is not None and all(type(v) is bool for v in vector.values()),
                                 label=old_row["label"], label_origin="reused finite exhaustive development label",
                                 old_diagnostic=old_row["prediction"]["diagnostic"],
                                 updated_probability=None, operational="abstain", deletion_authorized=False))
        save(out/"enriched_records.json", enriched)
        old_report = json.loads(old_files["report.json"].read_text())
        old_dangerous = old_report["summary"]["diagnostic_classification"]["false_removal_ids"]
        by_id = {f["id"]: f for f in facts}
        control_evidence = [name for name in old_dangerous if by_id[name]["features"]["module_controls_observation"] is True
                            or by_id[name]["features"]["module_may_return"] is True]
        unknown = [name for name in old_dangerous if any(v is None for v in by_id[name]["features"].values())]
        remaining = [name for name in old_dangerous if name not in control_evidence and name not in unknown]
        assert by_id["control_dependency"]["features"]["module_controls_observation"] is True
        assert by_id["early_return"]["features"]["module_may_return"] is True
        assert by_id["opposite_guard"]["features"]["retained_action_same_guard_context"] is False
        assert by_id["redundant_guard"]["features"]["retained_action_same_guard_context"] is True
        assert by_id["implicit_division_error"]["status"] == "partial"
        assert digest(out/"sealed_control_facts.json") == sealed_hash
        for name, expected in frozen_files.items():
            assert digest(out/name) == expected, name
        for name, expected in original_hashes.items():
            assert digest(BASE/name) == expected, name
        report.update(status="completed", protocol=protocol, cases=len(facts), sealed_control_facts_sha256=sealed_hash,
                      analysis_status_counts={status: sum(f["status"] == status for f in facts)
                                              for status in sorted({f["status"] for f in facts})},
                      complete_enriched_vectors=sum(r["feature_vector_complete"] for r in enriched),
                      old_dangerous_candidates=old_dangerous,
                      old_dangerous_with_control_evidence=control_evidence,
                      old_dangerous_with_unknown_effects=unknown,
                      old_dangerous_without_new_control_evidence=remaining,
                      unchanged_v1_hashes=original_hashes,
                      interpretation="Feature development regression only. Evidence coverage is not corrected error rate, probability calibration or deletion authorization.")
    except BaseException as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        save(out/"report.json", report)
        save(out/"manifest.json", {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob("*"))
                                  if p.is_file() and "__pycache__" not in p.parts and p != out/"manifest.json"})
        print(json.dumps(dict(status=report["status"], output=str(out), unit_tests=report.get("unit_tests"),
                              counts=report.get("analysis_status_counts"),
                              control_evidence=report.get("old_dangerous_with_control_evidence"),
                              unknown=report.get("old_dangerous_with_unknown_effects"),
                              remaining=report.get("old_dangerous_without_new_control_evidence")), ensure_ascii=False))


if __name__ == "__main__":
    main()
