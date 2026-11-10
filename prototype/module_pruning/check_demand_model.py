"""Bounded offline review of manual source bindings and a dependency model."""
import copy
import hashlib
import itertools
import json
import shutil
import uuid
from pathlib import Path

from demand_model import OBLIGATIONS, decide, reachable

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "evidence/module-pruning/20261104-490b6c6cef18"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    graph_path=Path(__file__).with_name("demand_graph.json")
    graph=json.loads(graph_path.read_text())
    profile=graph["declared_profile"]
    manifest=json.loads((PILOT/"manifest.json").read_text())
    sources={
        "procedure":"inputs/evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/OrderStatus.java",
        "adapter":"inputs/evidence/tpcc-observation/20261103-8729d2283fdc/run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java"
    }
    paths=[PILOT/p for p in sources.values()]+[PILOT/"results.json",PILOT/"run.json",PILOT/"deletion.diff"]
    for path in paths:
        if digest(path)!=manifest[str(path.relative_to(PILOT))]:
            raise ValueError("Archived evidence changed: "+str(path))
    old=json.loads((PILOT/"results.json").read_text())
    assert old["fault_counterexample"]["original"]["status"]=="error"
    assert old["fault_counterexample"]["pruned"]["status"]=="ok"
    assert old["read_counts"]["original"]==5 and old["read_counts"]["pruned"]==4
    assert old["bindings"][0]["module_present"] and not old["bindings"][1]["module_present"]
    anchors=[]
    for name,source,fragment in [
        ("display-method","procedure","   private List<String> getOrderLines("),
        ("dedicated-query","procedure","   public SQLStmt ordStatGetOrderLinesSQL ="),
        ("display-consumer","procedure","      List<String> orderLines = this.getOrderLines("),
        ("display-consumption","procedure","               for (String orderLine : orderLines) {"),
        ("customer-check","procedure","Failed to get CUSTOMER [C_W_ID=%d, C_D_ID=%d, C_ID=%d]"),
        ("order-check","procedure","No order records for CUSTOMER"),
        ("shared-helper-display","procedure","this.getPreparedStatement(conn, this.ordStatGetOrderLinesSQL)"),
        ("shared-helper-customer","procedure","this.getPreparedStatement(conn, this.payGetCustSQL)"),
        ("discarded-call","adapter",'        invoke(p,"getOrderLines",c,1,d,order.o_id,cust);'),
        ("order-consumer","adapter",'Oorder order=(Oorder)invoke(p,"getOrderDetails",c,1,d,cust);')
    ]:
        path=PILOT/sources[source]; text=path.read_text()
        assert text.count(fragment)==1,name
        anchors.append(dict(name=name,path=str(path.relative_to(ROOT)),line=text[:text.index(fragment)].count("\n")+1,fragment=fragment))
    # These are declared obligations, not inferred conclusions. Only this exact
    # module's boundary edits have a preceding build/runtime witness.
    certificate={key:True for key in OBLIGATIONS}
    cases=[]
    def check(name,candidate,expected,facts=None,cert=None):
        result=decide(graph,profile if facts is None else facts,graph["candidates"][candidate],certificate if cert is None else cert)
        assert result["decision"]==expected,(name,result)
        cases.append(dict(name=name,candidate=candidate,facts=profile if facts is None else facts,obligations=certificate if cert is None else cert,result=result))
    check("normal-display","display_module","conditionally_deletable")
    for candidate in ["whole_orderstatus_class","customer_module","latest_order_module","display_plus_shared_helper"]:
        check(candidate,candidate,"retain")
    for key in profile:
        check("enabled-"+key,"display_module","retain",facts={**profile,key:True})
    for key in ["legacy_failure_possible","external_reflection_possible"]:
        facts=dict(profile); del facts[key]
        check("unknown-"+key,"display_module","unknown",facts=facts)
    check("unclosed-graph","display_module","unknown",cert={**certificate,"dependency_summary_complete":None})
    check("field-only-without-caller-rewrite","sql_field_only","unknown",cert={**certificate,"boundary_edits_valid":False})
    check("source-binding-missing","display_module","unknown",cert={**certificate,"source_binding_valid":None})

    # Check uncertainty conservatively against all completions of three missing
    # facts. This validates this graph abstraction, not Java semantics.
    missing=["legacy_failure_possible","retain_terminal_display","observe_registry"]
    partial={k:v for k,v in profile.items() if k not in missing}
    lower=set(reachable(graph,partial,False)); upper=set(reachable(graph,partial,True))
    completions=[]
    for values in itertools.product([False,True],repeat=len(missing)):
        facts={**partial,**dict(zip(missing,values))}
        reached=set(reachable(graph,facts,False))
        assert lower<=reached<=upper
        completions.append(dict(facts=facts,required=sorted(reached)))
    # With all facts known, increasing a possible observation/effect cannot make
    # the required set shrink. 128 profiles, 448 one-flag extensions.
    keys=list(profile); monotonic_checks=0
    for values in itertools.product([False,True],repeat=len(keys)):
        facts=dict(zip(keys,values)); before=set(reachable(graph,facts,False))
        for key,value in facts.items():
            if not value:
                after=set(reachable(graph,{**facts,key:True},False))
                assert before<=after
                monotonic_checks+=1
    mutants=[]
    for kind,fact,witness in [("exception-control","legacy_failure_possible","archived query-failure outcome"),("read-effect","observe_queries","archived 5-versus-4 SQL trace")]:
        mutated=copy.deepcopy(graph)
        mutated["edges"]=[e for e in graph["edges"] if e["kind"]!=kind]
        bad=decide(mutated,{**profile,fact:True},graph["candidates"]["display_module"],certificate)
        assert bad["decision"]=="conditionally_deletable"
        mutants.append(dict(removed_dependency=kind,unsound_result=bad,rejected_by=witness))

    out=ROOT/"evidence/module-demand"/("20261104-"+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    inputs=[Path(__file__),Path(__file__).with_name("demand_model.py"),graph_path,*paths,PILOT/"manifest.json"]
    for path in inputs:
        target=out/"inputs"/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
    report=dict(status="passed",scope="finite supplied-graph analysis and manual source binding; no new database execution or automatic inference",
                bindings=anchors,cases=cases,uncertainty_completions=completions,monotonic_checks=monotonic_checks,mutants=mutants,
                obligations_status={"source_binding_valid":"hash/anchor check", "dependency_summary_complete":"manual assumption, not established by tests", "scope_assumptions_declared":"manual experimental scope", "boundary_edits_valid":"prior exact display-module build/runtime witness, not arbitrary subsets"},
                prior_manifest_sha256=digest(PILOT/"manifest.json"))
    (out/"analysis.json").write_text(json.dumps(report,indent=2)+"\n")
    (out/"manifest.json").write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()},indent=2)+"\n")
    print(json.dumps(dict(status="passed",output=str(out),decision_cases=len(cases),uncertainty_completions=len(completions),monotonic_checks=monotonic_checks,detected_unsound_models=len(mutants))))


if __name__=="__main__":
    run()
