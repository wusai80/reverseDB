#!/usr/bin/env python3
"""Collect one source-boundary label batch without changing apps or models."""
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/"prototype/module_pruning"
SOURCE_PATHS = {
    "paperless-ngx":["src/documents/bulk_download.py","src/documents/views.py","src/documents/serialisers.py"],
    "saleor":["saleor/checkout/utils.py","saleor/graphql/checkout/mutations/utils.py","saleor/core/models.py"],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")


def main():
    out = ROOT/"evidence/module-source-labels"/("20261104-"+uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    report = dict(status="running",production_authorized=False,refits=0,new_database_runs=0,new_performance_runs=0)
    frozen_files = {}; sources = {}; model_hashes = {}; source_hashes = {}
    def freeze(path):
        destination = out/"inputs"/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
        frozen_files[str(destination.relative_to(out))] = digest(destination)
        return destination
    try:
        protocol = json.loads((BASE/"source_label_protocol.json").read_text())
        locks = json.loads((ROOT/"sources.lock.json").read_text())
        freeze(ROOT/"sources.lock.json")
        provenance = []
        for project,paths in SOURCE_PATHS.items():
            repo = ROOT/"sources"/project
            locked = next(r for r in locks["repositories"] if r["name"]==project)
            head = subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            if head!=locked["commit"]:
                raise RuntimeError("Source revision drift: "+project)
            for relative in paths:
                path = repo/relative
                committed = subprocess.check_output(["git","-C",str(repo),"show",head+":"+relative])
                if committed!=path.read_bytes():
                    raise RuntimeError("Source differs from pinned commit: "+str(path))
                sources[(project,relative)] = freeze(path)
                source_hashes[str(path.relative_to(ROOT))] = digest(path)
            provenance.append(dict(project=project,commit=head,files=paths,source_origin="third_party_local_pinned_checkout",
                                   prior_exposure="Project existed in earlier analysis; these selected regions were not feature-fitting rows. Same researcher selects/adjudicates this batch."))
            if (repo/"LICENSE").is_file():
                freeze(repo/"LICENSE")
        for name in ["risk_model.json","effect_features.py","control_features.py","shared_risk.py","shared_risk_protocol.json"]:
            model_hashes[name] = digest(BASE/name)
            freeze(BASE/name)
        for name in ["source_labels.py","source_label_protocol.json","test_source_labels.py"]:
            freeze(BASE/name)
        freeze(Path(__file__))
        save(out/"freeze.json",frozen_files)
        frozen = out/"inputs/prototype/module_pruning"
        command = [sys.executable,"-B","-m","unittest","discover","-s",str(frozen),"-p","test_source_labels.py","-v"]
        result = subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/"tests.log").write_text(result.stdout)
        if result.returncode:
            raise RuntimeError("Source label boundary checks failed; see tests.log")
        report.update(unit_tests=int(re.search(r"Ran (\d+) tests",result.stdout).group(1)),test_command=command)
        sys.path.insert(0,str(frozen))
        from source_labels import build_mutant, proof_for_contract, decide_label, zip_scenarios, zip_observation, saleor_scenarios, saleor_observation, saleor_support
        zip_source = sources[("paperless-ngx","src/documents/bulk_download.py")].read_text()
        saleor_source = sources[("saleor","saleor/checkout/utils.py")].read_text()
        dto_source = sources[("saleor","saleor/graphql/checkout/mutations/utils.py")].read_text()
        metadata_source = sources[("saleor","saleor/core/models.py")].read_text()
        support = saleor_support(dto_source,metadata_source)
        mutations = {}; cases = []
        # Freeze every mutation/contract argument before executing any comparison.
        for case in protocol["corpus"]:
            key = (case["project"],case["candidate"])
            if key not in mutations:
                mutation = build_mutant(zip_source if key[0]=="paperless-ngx" else saleor_source,*key)
                mutations[key] = mutation
                directory = out/"mutations"/(key[0]+"-"+key[1]); directory.mkdir(parents=True)
                for variant in ["original","mutant"]:
                    (directory/(variant+".py")).write_text(mutation[variant+"_source"])
                patch = ''.join(difflib.unified_diff(mutation["original_source"].splitlines(True),mutation["mutant_source"].splitlines(True),fromfile="original.py",tofile="mutant.py"))
                (directory/"deletion.diff").write_text(patch)
                save(directory/"source_binding.json",dict(region=mutation["region"],proof=mutation["proof"],source_project=key[0],candidate=key[1]))
            proof = proof_for_contract(case,mutations[key],dto_source)
            cases.append(dict(case,source_argument=proof))
        save(out/"sealed_contracts.json",dict(protocol=protocol,provenance=provenance,cases=cases))
        # Include generated source/mutation evidence in a pre-execution seal.
        sealed = {str(p.relative_to(out)):digest(p) for p in sorted((out/"mutations").rglob("*")) if p.is_file()}
        sealed["sealed_contracts.json"] = digest(out/"sealed_contracts.json")
        save(out/"mutation_seal.json",sealed)
        rows = []; total_pairs = 0
        with tempfile.TemporaryDirectory(prefix="reverseDB-labels-") as temporary:
            for case in cases:
                mutation = mutations[(case["project"],case["candidate"])]
                states = zip_scenarios() if case["project"]=="paperless-ngx" else saleor_scenarios(case)
                pairs = []
                for index,state in enumerate(states):
                    if case["project"]=="paperless-ngx":
                        before = zip_observation(mutation["original_tree"],case,state,Path(temporary))
                        after = zip_observation(mutation["mutant_tree"],case,state,Path(temporary))
                    else:
                        before = saleor_observation(mutation["original_tree"],support,case,state)
                        after = saleor_observation(mutation["mutant_tree"],support,case,state)
                    if before["error"] is not None:
                        raise RuntimeError("Original boundary failed: "+case["id"]+" "+str(index)+" "+repr(before["error"]))
                    pairs.append(dict(input=state,original=before,deleted=after,equal=before==after))
                total_pairs += len(pairs)
                decision = decide_label(pairs,case["source_argument"])
                if case["source_argument"]["established"] and decision["label"]==1:
                    raise RuntimeError("Source argument contradicted by execution: "+case["id"])
                row = dict(case,**decision,source_group=case["project"],candidate_group=case["project"]+"/"+case["candidate"],
                           comparisons=pairs,full_application_label=None,label_scope="declared_source_boundary_only",
                           features=None,score=dict(status="abstain",reason="source_boundary_outside_finite_fixture_feature_domain",probability=None),
                           training_admitted=False,deletion_authorized=False)
                rows.append(row)
                save(out/"labels.json",rows)
        for name,expected in {**frozen_files,**sealed}.items():
            if digest(out/name)!=expected:
                raise RuntimeError("Frozen input drift: "+name)
        for name,expected in model_hashes.items():
            assert digest(BASE/name)==expected,name
        for name,expected in source_hashes.items():
            assert digest(ROOT/name)==expected,name
        report.update(status="completed",protocol=protocol,provenance=provenance,source_projects=2,candidate_regions=len(mutations),
                      contract_records=len(rows),label_counts={str(label):sum(r["label"]==label for r in rows) for label in [0,1,None]},
                      comparison_pairs=total_pairs,boundary_executions=total_pairs*2,full_application_labels=0,
                      executable_counterexamples=sum(r["counterexample"] is not None for r in rows),
                      conditional_source_arguments=sum(r["source_argument"]["established"] for r in rows),
                      operational_abstentions=len(rows),training_admitted=0,model_hashes=model_hashes,source_hashes=source_hashes,
                      limitations=["conditional capability contracts have not been deployed/enforced in full apps","document/ORM model interfaces are bounded adapters; Saleor has no database execution","no real-source feature extraction or model calibration","same researcher selected and adjudicated; not blind validation"])
    except BaseException as error:
        report.update(status="failed",error=repr(error))
        raise
    finally:
        save(out/"report.json",report)
        save(out/"manifest.json",{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*"))
                                 if p.is_file() and "__pycache__" not in p.parts and p!=out/"manifest.json"})
        print(json.dumps(dict(status=report["status"],output=str(out),unit_tests=report.get("unit_tests"),
                              labels=report.get("label_counts"),comparison_pairs=report.get("comparison_pairs")),ensure_ascii=False))


if __name__=="__main__":
    main()
