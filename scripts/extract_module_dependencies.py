#!/usr/bin/env python3
"""One offline attributed-AST extraction and bounded validation; no application run."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from app_db_smoke import ROOT, JDK, ARCHIVE, LIB, digest


def sql_shape(sql):
    """Only a deliberately small SELECT grammar. Even accepted reads aren't pure."""
    if not isinstance(sql,str):
        return dict(shape="unknown",reason="nonconstant_sql",effect="unknown")
    column=r"[A-Za-z_][A-Za-z_0-9]*"
    projection=rf"(?:\*|{column}(?:\s*,\s*{column})*)"
    value=r"(?:\?|[0-9]+)"
    comparison=rf"{column}\s*(?:=|<>|<=|>=|<|>)\s*{value}"
    where=rf"(?:\s+WHERE\s+{comparison}(?:\s+AND\s+{comparison})*)?"
    order=rf"(?:\s+ORDER\s+BY\s+{column}(?:\s+(?:ASC|DESC))?)?"
    limit=r"(?:\s+LIMIT\s+[0-9]+)?"
    match=re.fullmatch(rf"\s*SELECT\s+{projection}\s+FROM\s+({column}){where}{order}{limit}\s*",sql,re.I)
    if match:
        return dict(shape="restricted_select",table=match.group(1),effect="read_shape_only; JDBC failures, RLS, views, locks and observers unresolved")
    token=re.match(r"\s*([A-Za-z]+)",sql)
    if token and token.group(1).upper() in {"INSERT","UPDATE","DELETE","MERGE"}:
        return dict(shape="write_keyword",effect="may_write; do_not_delete_on_discarded_return")
    return dict(shape="unknown",reason="outside_restricted_grammar",effect="unknown")


def main():
    out=ROOT/"evidence/module-extraction"/("20261104-"+uuid.uuid4().hex[:12]); out.mkdir(parents=True)
    report=dict(status="running",commands=[],inputs={},retained_assets={},checks=[])
    def freeze(path):
        target=out/"inputs"/path.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        report["inputs"][str(path.relative_to(ROOT))]=digest(target)
        return target
    def run(command,log,expect_success=True):
        command=list(map(str,command)); report["commands"].append(command)
        with (out/log).open("w") as f:
            result=subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,timeout=120)
        if expect_success and result.returncode:
            raise RuntimeError("Command failed: "+str(out/log))
        return result.returncode
    def check(name,condition,**evidence):
        if not condition: raise AssertionError(name)
        report["checks"].append(dict(name=name,**evidence))
    try:
        manifest=json.loads((ARCHIVE/"manifest.json").read_text()); retained=json.loads((ARCHIVE/"retained-assets.json").read_text())
        bundle=ARCHIVE/"bundle"
        procedure=bundle/"sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/OrderStatus.java"
        obs=ROOT/"evidence/tpcc-observation/20261103-8729d2283fdc"
        adapter=obs/"run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java"
        obs_manifest=json.loads((obs/"manifest.json").read_text())
        assert digest(procedure)==manifest[str(procedure.relative_to(ARCHIVE))]
        assert digest(adapter)==obs_manifest[str(adapter.relative_to(obs))]
        dependencies=[bundle/"reference.jar",*sorted((bundle/"candidate-classes").rglob("*.class")),*sorted(LIB.glob("*.jar"))]
        for path in dependencies:
            expected=manifest[str(path.relative_to(ARCHIVE))] if path.is_relative_to(ARCHIVE) else retained[str(path)]
            assert digest(path)==expected,path
            report["retained_assets"][str(path)]=expected
        base=ROOT/"prototype/module_pruning"
        sources=[freeze(p) for p in [procedure,adapter,base/"DependencyFixture.java"]]
        extractor=freeze(base/"DependencyExtract.java")
        for p in [Path(__file__),ROOT/"scripts/app_db_smoke.py",base/"demand_model.py",base/"demand_graph.json",ARCHIVE/"manifest.json",ARCHIVE/"retained-assets.json",obs/"manifest.json"]: freeze(p)
        previous=ROOT/"evidence/module-extraction/20261104-afc97be01c4e"
        previous_manifest=json.loads((previous/"manifest.json").read_text())
        for name in ["analysis.json","extracted.json"]:
            assert digest(previous/name)==previous_manifest[name],name
            freeze(previous/name)
        freeze(previous/"manifest.json")
        # Manual semantic summary of the existing dispatcher, pinned to its exact
        # method bytes. No claim that its semantics were automatically inferred.
        adapter_text=sources[1].read_text()
        method=adapter_text[adapter_text.index("  static Object invoke("):adapter_text.index("  static JSONObject reference(")].strip()
        bridge=dict(symbol="com.oltpbenchmark.api.TpccApp#invoke(java.lang.Object,java.lang.String,java.lang.Object...)",
                    method_sha256=hashlib.sha256(method.encode()).hexdigest(),summary="declared methods matched by literal name and argument count; ambiguity/missing targets may throw")
        (out/"bridge-contract.json").write_text(json.dumps(bridge,indent=2)+"\n")
        classes=out/"extractor-classes"; classes.mkdir()
        jsonjar=LIB/"json-20240303.jar"
        run([JDK/"javac","--release","23","-proc:none","-cp",jsonjar,"-d",classes,extractor],"compile.log")
        cp=":".join(map(str,[bundle/"reference.jar",bundle/"candidate-classes",*sorted(LIB.glob("*.jar"))]))
        command=[JDK/"java","-cp",str(classes)+":"+str(jsonjar),"research.DependencyExtract",out/"extracted.json",cp,out/"bridge-contract.json",*sources]
        run(command,"extract.log")
        data=json.loads((out/"extracted.json").read_text())
        reflected=[c for c in data["calls"] if "reflection" in c]
        def refl_in(method):
            found=[c for c in reflected if "DependencyFixture#"+method+"(" in c["caller"]]
            assert len(found)==1,(method,len(found))
            return found[0]
        actual=[c for c in reflected if c["caller"].startswith("com.oltpbenchmark.api.TpccApp#reference(") and c["reflection"].get("requested_name")=="getOrderLines"]
        check("actual_discarded_reflective_call",len(actual)==1 and actual[0]["result_use"]["kind"]=="discarded",location=actual[0]["location"])
        check("actual_bounded_target",len(actual[0]["reflection"]["targets"])==1 and "OrderStatus#getOrderLines(" in actual[0]["reflection"]["targets"][0],reflection=actual[0]["reflection"])
        current=actual[0]["reflection"]
        old_data=json.loads((previous/"extracted.json").read_text())
        old=next(c["reflection"] for c in old_data["calls"] if "TpccApp#reference(" in c["caller"] and c.get("reflection",{}).get("requested_name")=="getOrderLines")
        check("same_original_unconditioned_receivers",sorted(current["unconditioned_receiver_types"])==sorted(old["receiver_types"]) and len(old["receiver_types"])==4)
        check("actual_branch_context",current["receiver_types"]==["com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus"] and current["path_sensitive"] and len(current["context_witnesses"])==1 and not current["no_matching_method"] and current["invocation_may_fail"],witness=current["context_witnesses"])
        direct=[c for c in data["calls"] if "OrderStatus#run(" in c["caller"] and "OrderStatus#getOrderLines(" in c["target_symbol"]]
        check("second_consumer_preserved",len(direct)==1 and direct[0]["result_use"]["kind"]=="assigned_local" and len(direct[0]["result_use"]["symbol_references"])>=1,location=direct[0]["location"])
        reads=[c for c in data["calls"] if "OrderStatus#getOrderLines(" in c["caller"] and c["sql_field_arguments"]]
        check("display_query_binding",len(reads)==1 and reads[0]["sql_field_arguments"]==["com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus#ordStatGetOrderLinesSQL"])
        sqls=[{**f,"classification":sql_shape(f["constant_sql"])} for f in data["sql_fields"]]
        selected=next(f for f in sqls if f["symbol"].endswith("#ordStatGetOrderLinesSQL"))
        check("display_read_shape",selected["classification"]["shape"]=="restricted_select" and selected["classification"]["table"]=="order_line")
        declared=next(m for m in data["methods"] if "OrderStatus#getOrderLines(" in m["symbol"])
        check("checked_exception_obligation","java.sql.SQLException" in declared["declared_throws"])
        for method in ["getOrderDetails","getCustomerById"]:
            check("business_error_"+method,any("OrderStatus#"+method+"(" in e["method"] and e["kind"]=="explicit_throw" for e in data["effects"]))
        factory=[c for c in data["calls"] if "#getPreparedStatement(" in c["target_symbol"] and "OrderStatus#" in c["caller"]]
        check("shared_factory_consumers",any("#getOrderLines(" in c["caller"] for c in factory) and any("#getCustomerById(" in c["caller"] for c in factory),callers=sorted(set(c["caller"] for c in factory)))
        for method in ["renamedDiscard","alias"]:
            c=refl_in(method)
            check(method,c["result_use"]["kind"]=="discarded" and len(c["reflection"].get("targets",[]))==1)
        c=refl_in("retainedReturn")
        check("retained_result",c["result_use"]["kind"]=="assigned_local" and len(c["result_use"]["symbol_references"])==1)
        for method,reason in [("dynamicName","dynamic_method_name"),("reassigned","receiver_points_to_unknown"),("unknownReceiver","receiver_points_to_unknown"),("packedArgs","packed_varargs"),("anonymous","receiver_points_to_unknown")]:
            r=refl_in(method)["reflection"]
            check(method,r["status"]=="unknown" and r["reason"]==reason)
        r=refl_in("ambiguous")["reflection"]
        check("ambiguous_overloads",len(r["targets"])==2 and len(r["ambiguous_receiver_types"])==1)
        for method,expected in [("branchEast","RenderBox"),("branchWest","OtherBox")]:
            r=refl_in(method)["reflection"]
            check(method,len(r["unconditioned_receiver_types"])==2 and r["receiver_types"]==["com.oltpbenchmark.api.DependencyFixture."+expected] and r["path_sensitive"])
        for method in ["selectorChanged","differentSelector","fallthrough","deferred","consumerDefault"]:
            r=refl_in(method)["reflection"]
            check(method,len(r["receiver_types"])==2 and not r["path_sensitive"] and not r["context_witnesses"])
        r=refl_in("receiverChanged")["reflection"]
        check("receiverChanged",r["status"]=="unknown" and r["reason"]=="receiver_points_to_unknown")
        r=refl_in("multipleLabels")["reflection"]
        check("multipleLabels",len(r["receiver_types"])==2 and r["path_sensitive"] and r["context_witnesses"][0]["labels"]==["east","west"])
        fixtures={f["symbol"].split("#")[-1]:f for f in sqls if "DependencyFixture#" in f["symbol"]}
        for field,shape in [("simple","restricted_select"),("hiddenFunction","unknown"),("writing","write_keyword"),("dynamic","unknown")]:
            check("sql_"+field,fixtures[field]["classification"]["shape"]==shape)
        check("format_substitution_not_reinterpreted",fixtures["literalPercent"]["constant_sql"]=="SELECT %s FROM records" and fixtures["literalPercent"]["classification"]["shape"]=="unknown")
        c=next(c for c in data["calls"] if "DependencyFixture#droppedButThrows(" in c["caller"] and "#checkedFailure(" in c["target_symbol"])
        check("discard_is_not_purity",c["result_use"]["kind"]=="discarded" and "java.sql.SQLException" in c["declared_throws"] and not c["effect_summary_complete"])
        # No promotion to an approved deletion: generated facts cannot establish
        # complete dependencies. Reuse the existing generic decision model.
        sys.path.insert(0,str(base))
        from demand_model import decide
        graph=json.loads((base/"demand_graph.json").read_text())
        decision=decide(graph,graph["declared_profile"],graph["candidates"]["display_module"],dict(source_binding_valid=True,scope_assumptions_declared=True,dependency_summary_complete=data["dependency_summary_complete"],boundary_edits_valid=True))
        check("cannot_auto_authorize_deletion",decision["decision"]=="unknown",result=decision)
        # Changing reflection semantics invalidates the registered summary.
        changed=out/"bridge-mutation"; changed.mkdir()
        modified=adapter_text.replace("target.getClass().getDeclaredMethods()","target.getClass().getMethods()")
        assert modified!=adapter_text
        (changed/"TpccApp.java").write_text(modified)
        bad=list(command); bad[-2]=changed/"TpccApp.java"; bad[4]=out/"mutation-should-not-exist.json"
        # command index 4 is the output after java -cp CP Main.
        rc=run(bad,"bridge-mutation.log",expect_success=False)
        check("bridge_change_rejected",rc!=0 and "Reflection bridge contract source changed" in (out/"bridge-mutation.log").read_text() and not (out/"mutation-should-not-exist.json").exists())
        report.update(status="passed",sql_shapes=sqls,automatic_decision=decision,
                      extracted_methods=len(data["methods"]),extracted_calls=len(data["calls"]),reflection_calls=len(reflected),
                      scope="two archived source inputs plus independent analysis fixtures; no Java application execution, database run or code deletion",
                      branch_comparison=dict(before=old,after=current),
                      binding_limit="only stable String selectors and arrow cases correlated; reflection bridge manual; graph/effect completeness remains false")
        for path,expected in report["retained_assets"].items(): assert digest(Path(path))==expected,path
    except BaseException as error:
        report.update(status="failed",error=repr(error)); raise
    finally:
        (out/"analysis.json").write_text(json.dumps(report,indent=2)+"\n")
        (out/"manifest.json").write_text(json.dumps({str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()},indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],output=str(out),checks=len(report["checks"]))))


if __name__=="__main__":
    main()
