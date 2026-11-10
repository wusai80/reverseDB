#!/usr/bin/env python3
"""One frozen source-native binding extraction and known-label consistency check."""
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
    out=ROOT/"evidence/module-strategy-bindings"/("20261104-"+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report=dict(status="running",production_authorized=False,refits=0,new_label_comparisons=0,
                new_database_runs=0,new_performance_runs=0,application_executions=0,http_caller_analysis="not_performed")
    frozen_files={}
    def freeze(path):
        destination=out/"inputs"/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
        frozen_files[str(destination.relative_to(out))]=digest(destination)
        return destination
    try:
        protocol=json.loads((BASE/"strategy_binding_protocol.json").read_text())
        old=ROOT/protocol["source_archive"]
        manifest=json.loads((old/"manifest.json").read_text())
        paths={}
        for name in ["report.json","labels.json","inputs/"+protocol["source_file"],
                     "mutations/paperless-ngx-formatted/source_binding.json","mutations/paperless-ngx-plain/source_binding.json"]:
            assert digest(old/name)==manifest[name],name
            paths[name]=freeze(old/name)
        freeze(old/"manifest.json")
        old_report=json.loads(paths["report.json"].read_text())
        source_path=ROOT/protocol["source_file"]
        assert digest(source_path)==old_report["source_hashes"][protocol["source_file"]]
        freeze(source_path)
        for name,sha in old_report["model_hashes"].items():
            assert digest(BASE/name)==sha,name
            freeze(BASE/name)
        for name in ["strategy_bindings.py","strategy_binding_protocol.json","test_strategy_bindings.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json",frozen_files)
        frozen=out/"inputs/prototype/module_pruning"
        command=[sys.executable,"-B","-m","unittest","discover","-s",str(frozen),"-p","test_strategy_bindings.py","-v"]
        tests=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(tests.stdout)
        if tests.returncode:
            raise RuntimeError("Strategy binding boundary checks failed; see tests.log")
        report.update(unit_tests=int(re.search(r"Ran (\d+) tests",tests.stdout).group(1)),test_command=command)
        sys.path.insert(0,str(frozen))
        from strategy_bindings import extract_bindings
        source=paths["inputs/"+protocol["source_file"]].read_text()
        facts={name:extract_bindings(source,protocol["roots"],domain) for name,domain in protocol["contexts"].items()}
        save(out/"sealed_binding_facts.json",facts)
        sealed_hash=digest(out/"sealed_binding_facts.json")
        if any(fact["status"]!="binding_facts_under_declared_scope" for fact in facts.values()):
            raise RuntimeError("Frozen source binding unsupported; inspect sealed facts")
        # Labels only enter after independent source/context extraction has sealed.
        labels=json.loads(paths["labels.json"].read_text())
        tree=ast.parse(source)
        candidate_ids={}
        for candidate in ["formatted","plain"]:
            binding=json.loads(paths[f"mutations/paperless-ngx-{candidate}/source_binding.json"].read_text())
            matches=[(cls.name,method) for cls in tree.body if isinstance(cls,ast.ClassDef)
                     for method in cls.body if isinstance(method,ast.FunctionDef) and method.lineno==binding["region"]["start"]]
            assert len(matches)==1
            cls,method=matches[0]
            assert hashlib.sha256(ast.dump(method,include_attributes=False).encode()).hexdigest()==binding["region"]["ast_sha256"]
            candidate_ids[candidate]=cls+"."+method.name
        checks=[]
        for row in labels:
            if row["project"]!="paperless-ngx":
                continue
            context="fixed_true" if row["follow_formatting"] else "fixed_false"
            definition=candidate_ids[row["candidate"]]
            fact=facts[context]
            reached=fact["candidate_reachability"][definition]
            paths_to_candidate=[edge for edge in fact["edges"] if edge["target"]==definition]
            checks.append(dict(id=row["id"],context=context,candidate_definition=definition,
                               structurally_reachable=reached,dependency_edges=paths_to_candidate,
                               archived_boundary_label=row["label"],consistent_with_archived_label=reached==bool(row["label"]),
                               full_application_label=None,probability=None,score_status="abstain",
                               training_admitted=False,deletion_authorized=False))
        save(out/"label_consistency.json",checks)
        assert all(check["consistent_with_archived_label"] for check in checks)
        assert facts["unknown_option"]["candidate_reachability"]==facts["explicit_both"]["candidate_reachability"]
        for definition in candidate_ids.values():
            assert facts["unknown_option"]["candidate_reachability"][definition]
        for fact in facts.values():
            assert not fact["effect_summary_complete"] and not fact["deletion_authorized"]
        assert digest(out/"sealed_binding_facts.json")==sealed_hash
        for name,expected in frozen_files.items():
            assert digest(out/name)==expected,name
        for name,expected in old_report["model_hashes"].items():
            assert digest(BASE/name)==expected,name
        assert digest(source_path)==old_report["source_hashes"][protocol["source_file"]]
        report.update(status="completed",protocol=protocol,source_sha256=digest(source_path),
                      sealed_binding_facts_sha256=sealed_hash,contexts=len(facts),concrete_roots=len(protocol["roots"]),
                      candidate_methods=len(candidate_ids),known_contract_records=len(checks),
                      consistent_records=sum(c["consistent_with_archived_label"] for c in checks),
                      per_context={name:dict(bindings=len(fact["bindings"]),
                          bind_time_edges=sum(e["kind"]=="bind_time_lookup" for e in fact["edges"]),
                          slot_call_edges=sum(e["kind"]=="bound_slot_call" for e in fact["edges"]),
                          unknown_external_calls=len(fact["external_calls"])) for name,fact in facts.items()},
                      unchanged_model_hashes=old_report["model_hashes"],operational_abstentions=len(checks),
                      limitations=["restricted single-inheritance binding grammar, not full Python dispatch analysis",
                                   "known-case source consistency, not independent prediction accuracy",
                                   "external effects and full HTTP/ORM context not closed",
                                   "new source-native schema not mapped into the old probability feature domain"])
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
