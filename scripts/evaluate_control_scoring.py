#!/usr/bin/env python3
"""One frozen common-cohort ablation, not model deployment or corpus expansion."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/"prototype/module_pruning"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")


def main():
    out = ROOT/"evidence/module-control-scoring"/("20261104-"+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status="running",production_authorized=False,new_label_comparisons=0,
                  new_database_runs=0,new_performance_runs=0)
    frozen_files = {}
    def freeze(path):
        destination = out/"inputs"/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
        frozen_files[str(destination.relative_to(out))] = digest(destination)
        return destination
    try:
        protocol = json.loads((BASE/"control_scoring_protocol.json").read_text())
        archive = ROOT/protocol["source_archive"]
        manifest = json.loads((archive/"manifest.json").read_text())
        originals = {}
        for name in ["enriched_records.json","sealed_control_facts.json","report.json"]:
            assert digest(archive/name)==manifest[name],name
            originals[name] = freeze(archive/name)
        freeze(archive/"manifest.json")
        old_report = json.loads(originals["report.json"].read_text())
        for name,expected in old_report["unchanged_v1_hashes"].items():
            assert digest(BASE/name)==expected,name
            freeze(BASE/name)
        for name in ["control_features.py","control_feature_protocol.json"]:
            assert digest(BASE/name)==manifest["inputs/prototype/module_pruning/"+name],name
            freeze(BASE/name)
        for name in ["control_scoring.py","control_scoring_protocol.json","test_control_scoring.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json",frozen_files)
        frozen = out/"inputs/prototype/module_pruning"
        command = [sys.executable,"-B","-m","unittest","discover","-s",str(frozen),"-p","test_control_scoring.py","-v"]
        tests = subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(tests.stdout)
        if tests.returncode:
            raise RuntimeError("Scoring isolation checks failed; see tests.log")
        report.update(unit_tests=int(re.search(r"Ran (\d+) tests",tests.stdout).group(1)),test_command=command)
        sys.path.insert(0,str(frozen))
        from control_scoring import evaluate, diagnostic_predict
        records = json.loads(originals["enriched_records.json"].read_text())
        evaluation = evaluate(records,protocol)
        save(out/"evaluation.json",evaluation)
        names = protocol["old_features"]+protocol["control_features"]
        for fold in evaluation["folds"]:
            for model in fold["models"].values():
                assert diagnostic_predict(model,{},"real-java-jdbc",names)["probability"] is None
                assert diagnostic_predict(model,{n:None for n in names},protocol["domain"],names)["probability"] is None
        excluded_ids = {r["id"] for r in evaluation["excluded"]}
        excluded_labels = [r["label"] for r in records if r["id"] in excluded_ids]
        for name,expected in frozen_files.items():
            assert digest(out/name)==expected,name
        for name,expected in old_report["unchanged_v1_hashes"].items():
            assert digest(BASE/name)==expected,name
        report.update(status="completed",protocol=protocol,coverage=evaluation["coverage"],
                      excluded_labels=dict(necessary=sum(excluded_labels),removable=len(excluded_labels)-sum(excluded_labels)),
                      active_mechanism_groups=evaluation["active_mechanism_groups"],independent_source_groups=1,
                      primary_evaluation_fits=sum(len(f["models"]) for f in evaluation["folds"]),
                      metrics={name:{key:summary[key] for key in ["macro_brier","macro_log_loss"]} for name,summary in evaluation["metrics"].items()},
                      unseen_cells=evaluation["unseen_cells"],diagnostic_classification=evaluation["diagnostic_classification"],
                      representation=evaluation["representation"],control_beats_old_on_both_metrics=evaluation["control_beats_old_on_both_metrics"],
                      operational_abstentions=len(records),authorized_deletions=0,
                      unchanged_v1_hashes=old_report["unchanged_v1_hashes"])
    except BaseException as error:
        report.update(status="failed",error=repr(error))
        raise
    finally:
        save(out/"report.json",report)
        save(out/"manifest.json",{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*"))
                                 if p.is_file() and "__pycache__" not in p.parts and p!=out/"manifest.json"})
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),
                              coverage=report.get("coverage"),metrics=report.get("metrics"),unseen_cells=report.get("unseen_cells")),ensure_ascii=False))


if __name__ == "__main__":
    main()
