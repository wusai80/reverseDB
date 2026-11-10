#!/usr/bin/env python3
"""Score the existing module case with explicit untrained assumptions; execute no deletion."""
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"prototype/module_pruning"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_id(contract):
    return "orderstatus-"+hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:16]


def main():
    out=ROOT/"evidence/module-risk"/("20261104-"+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status="running",inputs={},commands=[],deletion_authorized=False,new_database_runs=0,new_performance_samples=0)
    def freeze(path):
        target=out/"inputs"/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        report["inputs"][str(path.relative_to(ROOT))]=digest(target)
        return target
    try:
        pilot=ROOT/"evidence/module-pruning/20261104-490b6c6cef18"
        extracted=ROOT/"evidence/module-extraction/20261104-f2652809967a"
        dependencies={pilot:["run.json","results.json","inputs/prototype/module_pruning/protocol.json"],extracted:["analysis.json","extracted.json","inputs/prototype/module_pruning/demand_graph.json"]}
        for folder,names in dependencies.items():
            manifest=json.loads((folder/"manifest.json").read_text())
            for name in names:
                if digest(folder/name)!=manifest[name]: raise ValueError("Source archive mismatch: "+name)
                freeze(folder/name)
            freeze(folder/"manifest.json")
        if (BASE/"demand_graph.json").read_bytes()!=(extracted/"inputs/prototype/module_pruning/demand_graph.json").read_bytes():
            raise ValueError("Dependency graph differs from bound version")
        for path in [Path(__file__),*[BASE/name for name in ["risk_score.py","risk_model.json","test_risk_score.py","demand_model.py","demand_graph.json"]]]: freeze(path)
        frozen=out/"inputs/prototype/module_pruning"
        command=[sys.executable,"-m","unittest","discover","-s",str(frozen),"-p","test_risk_score.py","-v"]
        report["commands"].append(command)
        result=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(result.stdout)
        if result.returncode: raise RuntimeError("Risk score checks failed; see tests.log")
        report["unit_tests"]=int(re.search(r"Ran (\d+) tests",result.stdout).group(1))
        sys.path.insert(0,str(frozen))
        from risk_score import score_candidate,rank_candidates
        from demand_model import decide
        model=json.loads((frozen/"risk_model.json").read_text())
        graph=json.loads((frozen/"demand_graph.json").read_text())
        protocol=json.loads((pilot/"inputs/prototype/module_pruning/protocol.json").read_text())
        normal=dict(root=protocol["root"],observations=protocol["observations"],profile=graph["declared_profile"])
        faults={**normal,"profile":{**normal["profile"],"legacy_failure_possible":True}}
        normal_id,fault_id=contract_id(normal),contract_id(faults)
        run=json.loads((pilot/"run.json").read_text()); samples=json.loads((pilot/"results.json").read_text())
        static=json.loads((extracted/"extracted.json").read_text())
        call=next(c for c in static["calls"] if "TpccApp#reference(" in c["caller"] and c.get("reflection",{}).get("requested_name")=="getOrderLines")
        assert call["result_use"]["kind"]=="discarded" and len(call["reflection"]["receiver_types"])==1
        assert not static["dependency_summary_complete"] and call["reflection"]["invocation_may_fail"]
        assert run["status"]=="passed" and len(samples["checks"])==8 and len(samples["samples"])==180
        assert all(c["original"]==c["pruned"] for c in samples["checks"])
        assert samples["fault_counterexample"]["original"]["status"]=="error" and samples["fault_counterexample"]["pruned"]["status"]=="ok"
        provenance=lambda folder,name,selector:dict(path=str((folder/name).relative_to(ROOT)),sha256=digest(folder/name),selector=selector)
        evidence=[
            dict(id="display-static-region",module="display_module",contract=normal_id,kind="soft",group="static_result_use",source=provenance(extracted,"extracted.json","TpccApp.reference -> getOrderLines"),interpretation="manual evidence grouping; absence of consumed return does not establish absence of effects"),
            dict(id="display-original-campaign",module="display_module",contract=normal_id,kind="soft",group="differential_campaign",source=provenance(pilot,"results.json","checks and samples"),observed_cases=8,timing_calls=180,interpretation="ONE joint evidence group; no per-trial updates"),
            dict(id="display-query-fault",module="display_module",contract=fault_id,kind="counterexample",source=provenance(pilot,"results.json","fault_counterexample"),interpretation="refutes equivalence only in the contract that includes this failure")]
        improvements=[r["median_ms"]["original"]-r["median_ms"]["pruned"] for r in run["summary"]]
        gain=[min(improvements),statistics.median(improvements),max(improvements)]
        certificate=dict(source_binding_valid=True,dependency_summary_complete=False,scope_assumptions_declared=True,boundary_edits_valid=True)
        candidates=[]; scores=[]
        for module,nodes in graph["candidates"].items():
            decision=decide(graph,graph["declared_profile"],nodes,certificate)
            candidate=dict(module=module,contract=normal_id,gain_grid=gain if module=="display_module" else None,
                required_witnesses=decision.get("witnesses",{}) if decision["decision"]=="retain" else [],
                transformation_status="previous exact experimental artifact; effects incomplete" if module=="display_module" else "no scored transformation artifact",
                validation_eligible=module=="display_module")
            candidates.append({**candidate,"structural_decision":decision})
            scores.append(score_candidate(candidate,evidence,model))
        fault_candidate=dict(module="display_module",contract=fault_id,gain_grid=None,transformation_status="counterexample applies")
        fault_score=score_candidate(fault_candidate,evidence,model)
        assert fault_score["hard_block"] and fault_score["necessity_probability"]["central"]==1
        display=next(s for s in scores if s["module"]=="display_module")
        assert not display["hard_block"] and len(display["counted_groups"])==2
        assert all(s["hard_block"] for s in scores if s["module"] in {"customer_module","latest_order_module","whole_orderstatus_class","display_plus_shared_helper"})
        assert not any(s["deletion_authorized"] for s in scores+[fault_score])
        report.update(status="passed",model=model,contracts={normal_id:normal,fault_id:faults},evidence=evidence,candidates=candidates,scores=scores,
            expanded_fault_contract_assessment=fault_score,ranking=rank_candidates(scores),
            gain_provenance=dict(source=provenance(pilot,"run.json","summary"),observed_improvements_ms=improvements,grid=gain,meaning="min/median/max of three observed shape medians; NOT confidence limits"),
            scope="uncalibrated risk/utility and next-check suggestions only; evidence-to-group mapping and dependency graph remain manual",
            limitations=["No learned likelihoods or external calibration set","Cross-group conditional independence is assumed, not demonstrated","Sensitivity envelope is not a Bayesian credible interval","Validation sensitivity and costs are hypothetical; at most one next check is suggested, none executed","Only one real candidate has measured gain; remaining missing costs are not fabricated as zero"])
    except BaseException as error:
        report.update(status="failed",error=repr(error)); raise
    finally:
        (out/"scores.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        (out/"manifest.json").write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()},indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),deletion_authorized=False)))


if __name__=="__main__":
    main()
