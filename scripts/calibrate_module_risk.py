#!/usr/bin/env python3
"""One predeclared grouped calibration pilot; real application scores remain untouched."""
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
    out=ROOT/"evidence/module-calibration"/("20261104-"+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status="running",inputs={},commands=[],production_authorized=False,new_database_runs=0)
    def freeze(path):
        dst=out/"inputs"/path.relative_to(ROOT); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dst)
        report["inputs"][str(path.relative_to(ROOT))]=digest(dst)
        return dst
    try:
        old=ROOT/"evidence/module-risk/20261104-fec871587471"
        manifest=json.loads((old/"manifest.json").read_text())
        for name in ["scores.json","inputs/prototype/module_pruning/risk_model.json","inputs/prototype/module_pruning/risk_score.py"]:
            assert digest(old/name)==manifest[name],name
            freeze(old/name)
        freeze(old/"manifest.json")
        for name in ["calibration_protocol.json","calibration_suite.py","calibrate_risk.py","test_calibrate_risk.py"]: freeze(BASE/name)
        freeze(Path(__file__))
        frozen=out/"inputs/prototype/module_pruning"
        config_before=digest(BASE/"risk_model.json")
        report["existing_model_sha256_before"]=config_before
        protocol=json.loads((frozen/"calibration_protocol.json").read_text())
        old_report=json.loads((old/"scores.json").read_text())
        # All candidate modules/contracts overlap the same underlying program.
        audit=[]
        for row in old_report["scores"]:
            audit.append(dict(module=row["module"],contract=row["contract"],source_family="tpcc-orderstatus",
                label=None,status="required_by_manual_graph" if row["hard_block"] else "conditional_argument_or_unresolved",
                rationale="not an independently adjudicated executable removal label; passing tests alone do not label unnecessary"))
        fault=old_report["expanded_fault_contract_assessment"]
        assert fault["block_reason"]=="in_contract_counterexample"
        audit.append(dict(module=fault["module"],contract=fault["contract"],source_family="tpcc-orderstatus",label=1,status="observed_counterexample"))
        report["real_data_audit"]=dict(records=audit,independent_source_families=1,confirmed_positive_labels=1,confirmed_negative_labels=0,
            fit_status="insufficient_independent_labels",source=str((old/"scores.json").relative_to(ROOT)))
        command=[sys.executable,"-m","unittest","discover","-s",str(frozen),"-p","test_calibrate_risk.py","-v"]
        report["commands"].append(command)
        result=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(result.stdout)
        if result.returncode: raise RuntimeError("Calibration checks failed; see tests.log")
        report["unit_tests"]=int(re.search(r"Ran (\d+) tests",result.stdout).group(1))
        sys.path.insert(0,str(frozen))
        from calibration_suite import build_suite
        from calibrate_risk import fit,predict,grouped_evaluation
        rows=build_suite(protocol)
        (out/"dataset.json").write_text(json.dumps(dict(protocol=protocol,cases=rows),indent=2)+"\n")
        previous_model=json.loads((old/"inputs/prototype/module_pruning/risk_model.json").read_text())
        # Exact old central soft update; hard gates/oracle labels are NOT predictor inputs.
        def old_prediction(features):
            prior=previous_model["prior_grid"][1]; odds=prior/(1-prior)
            if features["discarded_result"]: odds*=previous_model["groups"]["static_result_use"]["bayes_factor_grid"][1]
            if features["limited_probe_pass"]: odds*=previous_model["groups"]["differential_campaign"]["bayes_factor_grid"][1]
            return odds/(1+odds)
        evaluation=grouped_evaluation(rows,protocol,old_prediction)
        (out/"evaluation.json").write_text(json.dumps(evaluation,indent=2)+"\n")
        fitted=fit(rows,protocol)
        (out/"fixture_model.json").write_text(json.dumps(fitted,indent=2)+"\n")
        real_prediction=predict(fitted,dict(discarded_result=True,limited_probe_pass=True),"real-orderstatus")
        assert real_prediction["status"]=="abstain" and real_prediction["probability"] is None
        assert digest(BASE/"risk_model.json")==config_before
        comparisons=sum(len(r["exhaustive_comparisons"]) for r in rows)
        report.update(status="passed",protocol=protocol,controlled_cases=len(rows),controlled_families=len(protocol["families"]),
            exhaustive_state_comparisons=comparisons,limited_probe_comparisons=len(rows),finite_program_evaluations=2*(comparisons+len(rows)),
            positive_labels=sum(r["label"] for r in rows),negative_labels=sum(1-r["label"] for r in rows),
            metrics=evaluation["metrics"],beats_both_baselines=evaluation["beats_both_baselines"],
            constant_features=fitted["constant_features"],real_domain_prediction=real_prediction,
            existing_model_sha256_after=digest(BASE/"risk_model.json"),
            empirical_status="grouped controlled-fixture pilot only; not real application calibration",
            limitations=["handcrafted balanced suite; mixture prevalence is not a deployment prior","finite Python program semantics, not decompiled Java/JDBC execution","variants within a family share a source mechanism and are not independent cases","labels are exhaustive only within each explicitly finite contract","no informative variation in the limited-pass feature","loss weights, performance gain and validation probe sensitivities were not calibrated","no tuning or new families after observing fold results"])
    except BaseException as error:
        report.update(status="failed",error=repr(error)); raise
    finally:
        (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        (out/"manifest.json").write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()},indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),production_authorized=False)))


if __name__=="__main__": main()
