#!/usr/bin/env python3
"""One fixed additive-model experiment on the prior grouped development split."""
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


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")


def main():
    out = ROOT/"evidence/module-shared-risk"/("20261104-"+uuid.uuid4().hex[:12])
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
        settings = json.loads((BASE/"shared_risk_protocol.json").read_text())
        old = ROOT/settings["source_archive"]
        manifest = json.loads((old/"manifest.json").read_text())
        saved = {}
        source_records = "inputs/evidence/module-control/20261104-951bae7ea42b/enriched_records.json"
        for name in ["evaluation.json","report.json",source_records]:
            assert digest(old/name)==manifest[name],name
            saved[name] = freeze(old/name)
        freeze(old/"manifest.json")
        old_report = json.loads(saved["report.json"].read_text())
        for name in ["calibrate_risk.py","control_scoring.py","control_scoring_protocol.json","control_features.py","effect_features.py","risk_model.json"]:
            assert digest(BASE/name)==manifest["inputs/prototype/module_pruning/"+name],name
            freeze(BASE/name)
        for name in ["shared_risk.py","shared_risk_protocol.json","test_shared_risk.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json",frozen_files)
        frozen = out/"inputs/prototype/module_pruning"
        command = [sys.executable,"-B","-m","unittest","discover","-s",str(frozen),"-p","test_shared_risk.py","-v"]
        result = subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(result.stdout)
        if result.returncode:
            raise RuntimeError("Additive model checks failed; see tests.log")
        report.update(test_command=command,unit_tests=int(re.search(r"Ran (\d+) tests",result.stdout).group(1)))
        sys.path.insert(0,str(frozen))
        from shared_risk import fit_shared, predict_shared
        from control_scoring import prepare, classification
        from calibrate_risk import metrics
        protocol = json.loads((frozen/"control_scoring_protocol.json").read_text())
        records = json.loads(saved[source_records].read_text())
        rows,excluded = prepare(records,protocol)
        previous = json.loads(saved["evaluation.json"].read_text())
        assert [r["id"] for r in rows]==previous["eligible_ids"]
        assert excluded==previous["excluded"]
        by_id = {r["id"]:r for r in rows}
        names = {"old_eight":protocol["old_features"],"with_control":protocol["old_features"]+protocol["control_features"]}
        forecasts = {name:[] for name in names}; folds = []
        for original in previous["folds"]:
            group = original["held_out_group"]
            train = [by_id[i] for i in original["train_ids"]]
            test = [by_id[i] for i in original["test_ids"]]
            assert all(r["family"]!=group for r in train) and all(r["family"]==group for r in test)
            fold = dict(held_out_group=group,train_ids=original["train_ids"],test_ids=original["test_ids"],models={})
            for arm in settings["feature_arms"]:
                model = fit_shared(train,protocol,names[arm],settings)
                assert model["training_prior"]==original["models"][arm]["prior"]
                assert model["training_ids"]==original["models"][arm]["training_ids"]
                assert predict_shared(model,{},"real-java-jdbc")["probability"] is None
                assert predict_shared(model,{},protocol["domain"])["probability"] is None
                fold["models"][arm] = model
                for row in test:
                    prediction = predict_shared(model,row["features"],row["domain"])
                    forecasts[arm].append(dict(id=row["id"],family=group,label=row["label"],**prediction))
            folds.append(fold)
        scores = {arm:metrics(predictions,protocol["reliability_bin_edges"]) for arm,predictions in forecasts.items()}
        decisions = {arm:classification(predictions,protocol["classification_cutoff"]) for arm,predictions in forecasts.items()}
        diagnostics = {arm:dict(changed_from_prior=sum(abs(p["probability"]-p["training_prior"])>1e-12 for p in predictions),
                                with_training_unseen_values=sum(bool(p["training_unseen_values"]) for p in predictions))
                       for arm,predictions in forecasts.items()}
        evaluation = dict(folds=folds,forecasts=forecasts,metrics=scores,diagnostic_classification=decisions,
                          sharing_diagnostics=diagnostics,excluded=excluded,coverage=previous["coverage"],
                          baseline_forecasts=previous["forecasts"],baseline_metrics=previous["metrics"],
                          operational_abstentions=len(records),authorized_deletions=0,production_authorized=False)
        save(out/"evaluation.json",evaluation)
        for name,expected in frozen_files.items():
            assert digest(out/name)==expected,name
        for name in ["calibrate_risk.py","control_scoring.py","control_features.py","effect_features.py","risk_model.json"]:
            assert digest(BASE/name)==digest(frozen/name),name
        report.update(status="completed",settings=settings,coverage=previous["coverage"],
                      additive_optimizer_fits=sum(len(f["models"]) for f in folds),
                      metrics={arm:{k:m[k] for k in ["macro_brier","macro_log_loss"]} for arm,m in scores.items()},
                      baseline_metrics=old_report["metrics"],diagnostic_classification=decisions,sharing_diagnostics=diagnostics,
                      control_beats_old_additive_on_both_metrics=all(scores["with_control"][k]<scores["old_eight"][k] for k in ["macro_brier","macro_log_loss"]),
                      control_beats_training_prior_on_both_metrics=all(scores["with_control"][k]<previous["metrics"]["training_prior"][k] for k in ["macro_brier","macro_log_loss"]),
                      operational_abstentions=len(records),authorized_deletions=0,unchanged_v1_hashes=old_report["unchanged_v1_hashes"])
    except BaseException as error:
        report.update(status="failed",error=repr(error))
        raise
    finally:
        save(out/"report.json",report)
        save(out/"manifest.json",{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*"))
                                 if p.is_file() and "__pycache__" not in p.parts and p!=out/"manifest.json"})
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),
                              metrics=report.get("metrics"),sharing_diagnostics=report.get("sharing_diagnostics")),ensure_ascii=False))


if __name__=="__main__":
    main()
