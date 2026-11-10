#!/usr/bin/env python3
"""Fixed feature ablation on an already-seen, frozen development dataset."""
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"prototype/module_pruning"


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out=ROOT/"evidence/module-effects"/("20261104-"+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status="running",inputs={},commands=[],production_authorized=False,new_database_runs=0,new_label_comparisons=0)
    def freeze(path):
        dst=out/"inputs"/path.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dst)
        report["inputs"][str(path.relative_to(ROOT))]=digest(dst)
        return dst
    try:
        old=ROOT/"evidence/module-calibration/20261104-00de1b003405"
        manifest=json.loads((old/"manifest.json").read_text())
        for name in ["dataset.json","evaluation.json","report.json"]:
            assert digest(old/name)==manifest[name],name
            freeze(old/name)
        freeze(old/"manifest.json")
        for name in ["calibration_suite.py","calibrate_risk.py","calibration_protocol.json"]:
            archived=old/"inputs/prototype/module_pruning"/name
            assert digest(archived)==manifest[str(archived.relative_to(old))]
            assert (BASE/name).read_bytes()==archived.read_bytes(),name
            freeze(BASE/name)
        for name in ["effect_feature_protocol.json","effect_features.py","test_effect_features.py"]: freeze(BASE/name)
        freeze(Path(__file__))
        frozen=out/"inputs/prototype/module_pruning"
        protocol=json.loads((frozen/"effect_feature_protocol.json").read_text())
        risk_before=digest(BASE/"risk_model.json")
        report["existing_risk_model_sha256_before"]=risk_before
        command=[sys.executable,"-m","unittest","discover","-s",str(frozen),"-p","test_effect_features.py","-v"]
        report["commands"].append(command)
        result=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(result.stdout)
        if result.returncode: raise RuntimeError("Effect feature checks failed; see tests.log")
        report["unit_tests"]=int(re.search(r"Ran (\d+) tests",result.stdout).group(1))
        sys.path.insert(0,str(frozen))
        from effect_features import extract
        from calibrate_risk import grouped_evaluation,fit,predict
        dataset=json.loads((old/"dataset.json").read_text())
        source=(frozen/"calibration_suite.py").read_text()
        rows=[]; feature_records=[]
        for original in dataset["cases"]:
            # Deliberate whitelist: oracle observations/labels never enter extract().
            bindings={"family":original["family"],"v":original["variant"]}
            domains={"x":sorted({s["value"] for s in original["allowed_states"]}),"flag":sorted({s["flag"] for s in original["allowed_states"]})}
            facts=extract(source,bindings,domains)
            feature_records.append(dict(id=original["id"],bindings=bindings,domains=domains,**facts))
            enriched=copy.deepcopy(original)
            enriched["features"].update(facts["features"])
            rows.append(enriched)
        (out/"feature_facts.json").write_text(json.dumps(feature_records,indent=2)+"\n")
        (out/"enriched_dataset.json").write_text(json.dumps(rows,indent=2)+"\n")
        for before,after in zip(dataset["cases"],rows):
            assert before["id"]==after["id"] and before["label"]==after["label"]
            assert before["exhaustive_comparisons"]==after["exhaustive_comparisons"]
        old_evaluation=json.loads((old/"evaluation.json").read_text())
        # Map the two frozen observed feature cells to the old frozen soft predictions.
        old_by_id={p["id"]:p["probability"] for p in old_evaluation["forecasts"]["frozen_v0_soft_score"]}
        old_by_features={}
        old_names=protocol["feature_sets"]["old_two"]
        for row in dataset["cases"]:
            key=tuple(row["features"][n] for n in old_names); q=old_by_id[row["id"]]
            if key in old_by_features: assert old_by_features[key]==q
            old_by_features[key]=q
        baseline=lambda features:old_by_features[tuple(features[n] for n in old_names)]
        evaluations={}; models={}
        for name,feature_names in protocol["feature_sets"].items():
            settings={**dataset["protocol"],"features":feature_names}
            evaluations[name]=grouped_evaluation(rows,settings,baseline)
            models[name]=fit(rows,settings)
            evaluations[name]["fallback_predictions"]=sum(predict(fold["model"],row["features"],row["domain"])["source"]=="training_prevalence"
                for fold in evaluations[name]["folds"] for row in rows if row["family"]==fold["held_out_family"])
            assert predict(models[name],rows[0]["features"],"real-orderstatus")["probability"] is None
        assert evaluations["old_two"]["metrics"]==old_evaluation["metrics"],"Old result drifted"
        (out/"evaluations.json").write_text(json.dumps(evaluations,indent=2)+"\n")
        (out/"fixture_models.json").write_text(json.dumps(models,indent=2)+"\n")
        assert digest(BASE/"risk_model.json")==risk_before
        report.update(status="passed",protocol=protocol,cases=len(rows),source_families=len({r["family"] for r in rows}),
            metrics={name:e["metrics"]["joint_calibrator"] for name,e in evaluations.items()},
            baseline_metrics=old_evaluation["metrics"],fallback_predictions={name:e["fallback_predictions"] for name,e in evaluations.items()},
            old_dataset_sha256=digest(old/"dataset.json"),existing_risk_model_sha256_after=digest(BASE/"risk_model.json"),
            scope="AST-derived effects on frozen finite Python programs; same grouped DEVELOPMENT evaluation, not independent holdout validation",
            limitations=["feature design was informed by previous results; grouped reuse is not fresh validation","only the supported finite fixture language is analyzed; not Java/JDBC effects","syntactically similar retained actions do not prove semantic replacement","no learned real-world probability or promotion","joint cells can become sparse as features increase; fallback counts reported"])
    except BaseException as error:
        report.update(status="failed",error=repr(error)); raise
    finally:
        (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        (out/"manifest.json").write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()},indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),production_authorized=False)))


if __name__=="__main__": main()
