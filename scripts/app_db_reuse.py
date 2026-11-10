#!/usr/bin/env python3
"""One bounded workflow-sharing experiment with frozen calibration/holdout."""
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest


def main():
    out = ROOT / "evidence/app-db-reuse" / ("20261104-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    name = "reversedb-reuse-" + out.name
    report = dict(status="running", container=name, commands=[], inputs={}, cleanup_errors=[])
    created = False

    def run(args, log, check=True, timeout=60):
        report["commands"].append([str(a) for a in args])
        result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        (out / log).write_text(result.stdout)
        if check and result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}); see {out / log}")
        return result

    def java_stage(args, log):
        report["commands"].append([str(a) for a in args])
        with (out / log).open("w") as stream:
            process = subprocess.Popen(args, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            deadline, last = time.monotonic()+300, ""
            try:
                while process.poll() is None:
                    lines = (out / log).read_text().splitlines()
                    if lines and lines[-1] != last:
                        last = lines[-1]
                        print(last, flush=True)
                    if time.monotonic() > deadline:
                        raise RuntimeError("Fixed experiment stage deadline exceeded")
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if process.returncode:
                raise RuntimeError(f"Java stage failed; see {out / log}")

    try:
        lock = ROOT / "work/paperless/images.lock.json"
        image = json.loads(lock.read_text())["images"]["postgres"]
        if run(DOCKER+["image", "inspect", image["resolved"], "--format", "{{.Id}}"], "image.log").stdout.strip() != image["id"]:
            raise RuntimeError("Pinned image mismatch")
        report["image"] = image
        old = ROOT / "evidence/app-db-costs/20261104-7ab7e225ccf0"
        previous = json.loads((old / "run.json").read_text())
        for name_in in ("DatabaseSmoke.java", "DatabaseCosts.java"):
            path = ROOT / "prototype/app_db_questions" / name_in
            if digest(path) != previous["inputs"][str(path.relative_to(ROOT))]:
                raise RuntimeError("Validated fixture dependency changed")
        manifest = json.loads((ARCHIVE / "manifest.json").read_text())
        retained = json.loads((ARCHIVE / "retained-assets.json").read_text())
        originals = [ARCHIVE / "bundle/sources/candidate/reversedb/tpcc" / f for f in ("StockLevelService.java", "Sql.java")]
        jars = [LIB / f for f in ("postgresql-42.7.4.jar", "json-20240303.jar")]
        for path in originals:
            if digest(path) != manifest[str(path.relative_to(ARCHIVE))]:
                raise RuntimeError("Decompiled dependency changed")
        for path in jars:
            if digest(path) != retained[str(path)]:
                raise RuntimeError("JAR dependency changed")
        sources = [ROOT / "prototype/app_db_questions" / f for f in ("DatabaseSmoke.java", "DatabaseCosts.java", "MemoryAgent.java", "WorkflowReuse.java")]
        protocol = ROOT / "prototype/app_db_questions/reuse_protocol.json"
        fitter = ROOT / "scripts/fit_workflow_model.py"
        inputs = [Path(__file__), ROOT / "scripts/app_db_smoke.py", fitter, *sources, *originals, *jars, protocol, lock,
                  old / "run.json", old / "manifest.json", ARCHIVE / "manifest.json", ARCHIVE / "retained-assets.json",
                  ROOT / "docs/APP_DB_REUSE_DESIGN.md"]
        for path in inputs:
            relative = path.relative_to(ROOT)
            copied = out / "inputs" / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
            report["inputs"][str(relative)] = digest(copied)
        def copied(path):
            return out / "inputs" / path.relative_to(ROOT)
        classes = out / "classes"
        classes.mkdir()
        cp = ":".join(str(copied(p)) for p in jars)
        run([JDK / "javac", "--release", "23", "-cp", cp, "-d", classes, *[copied(p) for p in [*originals,*sources]]], "compile.log")
        agent_manifest = out / "agent.mf"
        agent_manifest.write_text("Manifest-Version: 1.0\nPremain-Class: reversedb.tpcc.MemoryAgent\n\n")
        run([JDK / "jar", "--create", "--file", out / "memory-agent.jar", "--manifest", agent_manifest,
             "-C", classes, "reversedb/tpcc/MemoryAgent.class"], "agent-build.log")
        report["java_version"] = run([JDK / "java", "-version"], "java-version.log").stdout.strip()
        result = run(DOCKER+["run", "-d", "--pull=never", "--name", name, "--label", "reversedb.experiment=app-db-reuse",
                    "--memory=512m", "--cpus=1", "--tmpfs", "/var/lib/postgresql:rw,size=384m", "-p", "127.0.0.1::5432",
                    "-e", "POSTGRES_USER=research", "-e", "POSTGRES_PASSWORD=local-fixture-only", "-e", "POSTGRES_DB=research",
                    image["resolved"], "-c", "timezone=UTC"], "start.log")
        created = True
        report["container_id"] = result.stdout.strip()
        for attempt in range(30):
            if run(DOCKER+["exec",name,"pg_isready","-U","research","-d","research"], f"ready-{attempt}.log", check=False, timeout=5).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Database readiness timeout")
        address = run(DOCKER+["port",name,"5432/tcp"], "port.log").stdout.strip()
        if not address.startswith("127.0.0.1:") or "\n" in address:
            raise RuntimeError("Unexpected database exposure")
        url = "jdbc:postgresql://"+address+"/research?sslmode=disable&gssEncMode=disable&prepareThreshold=0&reWriteBatchedInserts=true"
        command = [JDK / "java", "-Xms64m", "-Xmx256m", "-XX:+UseSerialGC", "-javaagent:"+str(out / "memory-agent.jar"),
                   "-cp", str(classes)+":"+cp, "reversedb.tpcc.WorkflowReuse"]
        started = time.monotonic()
        java_stage(command+["calibrate",url,out/"calibration.json",copied(protocol)], "calibration.log")
        report["calibration_stage_wall_seconds"] = time.monotonic()-started
        started = time.monotonic()
        run(["python3",copied(fitter),out/"calibration.json",out/"model.json"], "fit.log")
        report["fit_wall_seconds"] = time.monotonic()-started
        report["model_sha256_before_evaluation"] = digest(out/"model.json")
        java_stage(command+["evaluate",url,out/"evaluation.json",copied(protocol),out/"model.json"], "evaluation.log")
        calibration = json.loads((out/"calibration.json").read_text())
        evaluation = json.loads((out/"evaluation.json").read_text())
        if calibration["status"] != "passed" or evaluation["status"] != "passed" or calibration["timed_samples"] != 120 or evaluation["timed_samples"] != 300:
            raise RuntimeError("Incomplete experiment matrix")
        if digest(out/"model.json") != report["model_sha256_before_evaluation"] or evaluation["model_sha256"] != report["model_sha256_before_evaluation"]:
            raise RuntimeError("Frozen model changed")
        report.update(status="passed", semantic_checks=len(calibration["semantic_checks"]), calibration_samples=120, evaluation_samples=300)
    except BaseException as error:
        report.update(status="failed",error=repr(error))
        raise
    finally:
        if created:
            for command, log in [(DOCKER+["logs",name],"postgres.log"),(DOCKER+["rm","-f","-v",name],"cleanup.log")]:
                try:
                    if run(command,log,check=False).returncode:report["cleanup_errors"].append(log)
                except Exception as error:
                    report["cleanup_errors"].append(repr(error))
        if report["cleanup_errors"]:report["status"]="failed"
        (out/"run.json").write_text(json.dumps(report,indent=2)+"\n")
        files={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*")) if p.is_file()}
        (out/"manifest.json").write_text(json.dumps(files,indent=2)+"\n")
        print(json.dumps(dict(status=report["status"],output=str(out),cleanup_errors=report["cleanup_errors"])),flush=True)
    if report["status"]!="passed":raise RuntimeError("Experiment did not finish cleanly")


if __name__=="__main__":
    main()
