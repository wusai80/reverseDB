#!/usr/bin/env python3
"""One static Saleor boundary extraction; no source mutation, labels or refits."""
import ast
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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")


def main():
    out=ROOT/"evidence/module-write-plan"/("20261104-"+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report=dict(status="running",production_authorized=False,refits=0,new_label_comparisons=0,
                new_database_runs=0,new_performance_runs=0,application_executions=0)
    frozen_files={}
    def freeze(path):
        destination=out/"inputs"/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
        frozen_files[str(destination.relative_to(out))]=digest(destination)
        return destination
    try:
        protocol=json.loads((BASE/"write_plan_protocol.json").read_text())
        old=ROOT/protocol["source_archive"]
        manifest=json.loads((old/"manifest.json").read_text())
        paths={}
        for name in ["report.json","labels.json","inputs/"+protocol["source_file"],"inputs/"+protocol["dto_file"],
                     "mutations/saleor-price/source_binding.json","mutations/saleor-reason/source_binding.json"]:
            assert digest(old/name)==manifest[name],name
            paths[name]=freeze(old/name)
        freeze(old/"manifest.json")
        old_report=json.loads(paths["report.json"].read_text())
        for name in [protocol["source_file"],protocol["dto_file"]]:
            assert digest(ROOT/name)==old_report["source_hashes"][name],name
            freeze(ROOT/name)
        for name,sha in old_report["model_hashes"].items():
            assert digest(BASE/name)==sha,name
            freeze(BASE/name)
        for name in ["write_plan_bindings.py","write_plan_protocol.json","test_write_plan_bindings.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json",frozen_files)
        frozen=out/"inputs/prototype/module_pruning"
        command=[sys.executable,"-B","-m","unittest","discover","-s",str(frozen),"-p","test_write_plan_bindings.py","-v"]
        tests=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(tests.stdout)
        if tests.returncode:
            raise RuntimeError("Write-plan boundary checks failed; see tests.log")
        report.update(unit_tests=int(re.search(r"Ran (\d+) tests",tests.stdout).group(1)),test_command=command)
        sys.path.insert(0,str(frozen))
        from write_plan_bindings import extract_write_plan
        source=paths["inputs/"+protocol["source_file"]].read_text()
        dto_source=paths["inputs/"+protocol["dto_file"]].read_text()
        facts={name:extract_write_plan(source,dto_source,protocol["binding"],domain) for name,domain in protocol["contexts"].items()}
        save(out/"sealed_write_plan_facts.json",facts)
        sealed_hash=digest(out/"sealed_write_plan_facts.json")
        for fact in facts.values():
            if fact["status"]!="local_write_facts_under_declared_scope" or fact["caller_link"]["status"]!="source_forwarding_identified":
                raise RuntimeError("Frozen source flow unsupported; see sealed facts")
        # The independent extractor has sealed before labels are joined below.
        labels=json.loads(paths["labels.json"].read_text())
        tree=ast.parse(source)
        candidate_regions={}
        for candidate in ["price","reason"]:
            binding=json.loads(paths[f"mutations/saleor-{candidate}/source_binding.json"].read_text())
            matches=[n for n in ast.walk(tree) if isinstance(n,ast.If) and n.lineno==binding["region"]["start"]]
            assert len(matches)==1
            node=matches[0]
            assert hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()==binding["region"]["ast_sha256"]
            candidate_regions[candidate]=(node.lineno,node.end_lineno)
        checks=[]
        for row in labels:
            if row["project"]!="saleor":
                continue
            context="price_capabilities_disabled" if row["contract"]=="disabled" else "price_capabilities_enabled"
            fact=facts[context]; start,end=candidate_regions[row["candidate"]]
            regions=[region for region in fact["guard_regions"] if region["line"]==start]
            assert len(regions)==1
            reached=regions[0]["body_may_reach"]
            writes=[write for write in fact["writes"] if start<=write["line"]<=end]
            checks.append(dict(id=row["id"],context=context,region=regions[0],writes=writes,
                               archived_boundary_label=row["label"],consistent_with_archived_label=reached==bool(row["label"]),
                               full_application_label=None,probability=None,score_status="abstain",training_admitted=False,deletion_authorized=False))
        save(out/"label_consistency.json",checks)
        assert len(checks)==4 and all(check["consistent_with_archived_label"] for check in checks)
        unknown=facts["unknown_flags"]; enabled=facts["price_capabilities_enabled"]
        assert [(w["line"],w["may_reach"]) for w in unknown["writes"]]==[(w["line"],w["may_reach"]) for w in enabled["writes"]]
        assert digest(out/"sealed_write_plan_facts.json")==sealed_hash
        for name,expected in frozen_files.items():
            assert digest(out/name)==expected,name
        for name,expected in old_report["model_hashes"].items():
            assert digest(BASE/name)==expected,name
        for name in [protocol["source_file"],protocol["dto_file"]]:
            assert digest(ROOT/name)==old_report["source_hashes"][name],name
        report.update(status="completed",protocol=protocol,source_sha256=digest(ROOT/protocol["source_file"]),
                      dto_sha256=digest(ROOT/protocol["dto_file"]),sealed_write_plan_facts_sha256=sealed_hash,
                      contexts=len(facts),known_contract_records=len(checks),consistent_records=sum(c["consistent_with_archived_label"] for c in checks),
                      per_context={name:dict(guard_regions=len(fact["guard_regions"]),write_sites=len(fact["writes"]),
                          reachable_write_sites=sum(w["may_reach"] for w in fact["writes"]),
                          potential_bulk_flow_sites=sum(w["may_flow_to_bulk_call"] is True for w in fact["writes"]),
                          direct_append_sites=sum(w["enqueue"]["kind"]=="direct_append" for w in fact["writes"]),
                          conditional_append_sites=sum(w["enqueue"]["kind"]=="append_if_not_member" for w in fact["writes"]),
                          unmodeled_projected_fields=sorted(set(fact["caller_link"]["projection_fields"])-{w["field"] for w in fact["writes"]})) for name,fact in facts.items()},
                      unchanged_model_hashes=old_report["model_hashes"],operational_abstentions=len(checks),
                      limitations=["source forwarding and may-flow are not commit/effect proofs","conditional list membership does not establish same-instance identity",
                                   "metadata method effect remains unknown; no missing-effect-to-unused inference","known labels reused; no new independent validation or score domain promotion"])
    except BaseException as error:
        report.update(status="failed",error=repr(error))
        raise
    finally:
        save(out/"report.json",report)
        save(out/"manifest.json",{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*"))
                                 if p.is_file() and "__pycache__" not in p.parts and p!=out/"manifest.json"})
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),
                              consistent_records=report.get("consistent_records"),per_context=report.get("per_context")),ensure_ascii=False))


if __name__=="__main__":
    main()
