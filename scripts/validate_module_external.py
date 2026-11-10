#!/usr/bin/env python3
"""One bounded, frozen challenge. Never trains, deletes a module or runs a DB."""
import hashlib
import itertools
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "prototype/module_pruning"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    out = ROOT / "evidence/module-external" / ("20261104-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status="running", production_authorized=False, new_database_runs=0,
                  new_performance_runs=0, refits=0)
    frozen_files = {}
    def freeze(path):
        target = out / "inputs" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        frozen_files[str(target.relative_to(out))] = digest(target)
        return target
    try:
        protocol = json.loads((BASE / "external_validation_protocol.json").read_text())
        old = ROOT / protocol["model_archive"]
        old_manifest = json.loads((old / "manifest.json").read_text())
        for name in ["fixture_models.json", "inputs/prototype/module_pruning/effect_features.py",
                     "inputs/prototype/module_pruning/calibrate_risk.py"]:
            assert digest(old/name) == old_manifest[name], name
            freeze(old/name)
        freeze(old / "manifest.json")
        for name in ["effect_features.py", "calibrate_risk.py"]:
            assert digest(BASE/name) == digest(old/"inputs/prototype/module_pruning"/name), name
        for name in ["external_validation_protocol.json", "external_corpus.py", "external_validation.py",
                     "external_real_review.json", "test_external_validation.py", "effect_features.py",
                     "calibrate_risk.py", "risk_model.json"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        risk_before = digest(BASE / "risk_model.json")
        real_archive = ROOT / "evidence/tpcc-roundtrip/20261103-9954559e225c"
        real_manifest = json.loads((real_archive / "manifest.json").read_text())
        freeze(real_archive / "manifest.json")
        java = {}
        for name in ["Payment", "NewOrder", "Delivery"]:
            path = real_archive / f"bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/{name}.java"
            assert digest(path) == real_manifest[str(path.relative_to(real_archive))], name
            java[name] = freeze(path)
        adapter_archive = ROOT / "evidence/tpcc-observation/20261103-8729d2283fdc"
        adapter = adapter_archive / "run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java"
        adapter_manifest = json.loads((adapter_archive/"manifest.json").read_text())
        assert digest(adapter) == adapter_manifest[str(adapter.relative_to(adapter_archive))]
        freeze(adapter_archive / "manifest.json")
        adapter = freeze(adapter)
        frozen = out / "inputs/prototype/module_pruning"
        sys.path.insert(0, str(frozen))
        from external_corpus import programs
        from external_validation import forecast, observe, summarize
        from calibrate_risk import predict
        corpus = programs()
        assert len(corpus) == protocol["program_count"]
        assert len({p["id"] for p in corpus}) == len(corpus)
        assert len({p["source"] for p in corpus}) == len(corpus)
        # Materialize every source and its metadata BEFORE any program execution.
        for case in corpus:
            path = out / "programs" / (case["id"] + ".py")
            path.parent.mkdir(exist_ok=True)
            path.write_text(case["source"])
            frozen_files[str(path.relative_to(out))] = digest(path)
        save(out / "corpus.json", corpus)
        frozen_files["corpus.json"] = digest(out / "corpus.json")
        save(out / "freeze.json", frozen_files)
        freeze_hash = digest(out / "freeze.json")
        model = json.loads((out/"inputs"/protocol["model_archive"]/"fixture_models.json").read_text())[protocol["model_name"]]
        report["freeze_sha256"] = freeze_hash

        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(frozen), "-p", "test_external_validation.py", "-v"]
        test = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        (out / "tests.log").write_text(test.stdout)
        if test.returncode:
            raise RuntimeError("Evaluation harness checks failed; see tests.log")
        report["test_command"] = command
        predictions = []
        functions = {}
        for case in corpus:
            env = {}
            exec(compile(case["source"], case["id"] + ".py", "exec"), env)
            functions[case["id"]] = env["execute"]
            before = observe(env["execute"], protocol["limited_probe"], False)
            after = observe(env["execute"], protocol["limited_probe"], True)
            prediction = forecast(case["source"], case["discarded_result"], before == after,
                                  protocol["input_domains"], model, protocol["challenge_domain"])
            predictions.append(dict(id=case["id"], prediction=prediction,
                                    probe=dict(input=protocol["limited_probe"], original=before, deleted=after)))
        save(out / "sealed_predictions.json", predictions)
        prediction_hash = digest(out / "sealed_predictions.json")
        # Only now may the exhaustive oracle inspect the rest of the finite domain.
        states = [dict(x=x, flag=flag) for x, flag in itertools.product(
            protocol["input_domains"]["x"], protocol["input_domains"]["flag"])]
        rows = []
        for entry in predictions:
            execute = functions[entry["id"]]
            pairs = []
            for state in states:
                before, after = observe(execute, state, False), observe(execute, state, True)
                pairs.append(dict(input=state, original=before, deleted=after, equal=before == after))
            witness = next((p for p in pairs if not p["equal"]), None)
            rows.append(dict(entry, label=int(witness is not None), counterexample=witness,
                             source_group=protocol["source_groups"]["finite"],
                             label_origin="exhaustive finite contract only", comparisons=pairs))
        assert digest(out / "sealed_predictions.json") == prediction_hash
        save(out / "results.json", rows)
        review = json.loads((frozen / "external_real_review.json").read_text())
        for item in review["modules"]:
            path = java[item["class"]]
            source = path.read_text()
            anchors = {}
            for field in ["method_anchor", "sql_anchor", "effect_anchor", "call_anchor", "root_anchor"]:
                if field not in item:
                    continue
                text = adapter.read_text() if field == "root_anchor" else source
                assert item[field] in text, (item["id"], field)
                anchors[field] = [i for i, line in enumerate(text.splitlines(), 1) if item[field] in line]
            item.update(source=str(path.relative_to(out)), source_sha256=digest(path),
                        adapter_source=str(adapter.relative_to(out)), adapter_sha256=digest(adapter),
                        anchor_lines=anchors, operational=predict(model, {}, "real-java-jdbc"),
                        deletion_authorized=False)
        save(out / "real_review.json", review)
        for name, sha in frozen_files.items():
            assert digest(out/name) == sha, name
        assert digest(out / "freeze.json") == freeze_hash
        assert digest(BASE / "risk_model.json") == risk_before
        for name in ["effect_features.py", "calibrate_risk.py"]:
            assert digest(BASE/name) == digest(frozen/name)
        summary = summarize(rows, protocol["classification_cutoff"])
        report.update(status="completed", protocol=protocol, summary=summary,
                      sealed_predictions_sha256=prediction_hash,
                      finite_exhaustive_pairs=len(rows)*len(states), limited_probe_pairs=len(rows),
                      pure_program_executions=2*len(rows)*(len(states)+1), real_modules=len(review["modules"]),
                      real_executable_labels=0, real_source_groups=1,
                      existing_risk_model_sha256_before=risk_before,
                      existing_risk_model_sha256_after=digest(BASE/"risk_model.json"))
    except BaseException as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        save(out / "report.json", report)
        save(out / "manifest.json", {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob("*"))
                                     if p.is_file() and "__pycache__" not in p.parts and p != out/"manifest.json"})
        print(json.dumps(dict(status=report["status"], output=str(out), summary=report.get("summary")), ensure_ascii=False))


if __name__ == "__main__":
    main()
