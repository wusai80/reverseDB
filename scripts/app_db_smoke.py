#!/usr/bin/env python3
"""One bounded semantic experiment using pinned local PostgreSQL/JDBC assets."""
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "--context", "desktop-linux"]
JDK = Path("/opt/homebrew/opt/openjdk/bin")
ARCHIVE = ROOT / "evidence/tpcc-roundtrip/20261103-9954559e225c"
LIB = ROOT / "work/tpcc/runs/c615d3357989/distribution/benchbase-postgres/lib"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / "evidence/app-db-smoke" / ("20261104-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    name = "reversedb-research-" + out.name
    report = dict(status="running", scope="small semantic fixtures only; no timing",
                  container=name, commands=[], inputs={}, cleanup_errors=[])
    created = False

    def run(args, log, check=True, timeout=60):
        report["commands"].append([str(a) for a in args])
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, cwd=ROOT)
        (out / log).write_text(result.stdout)
        if check and result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}); see {out / log}")
        return result

    try:
        lock = ROOT / "work/paperless/images.lock.json"
        image = json.loads(lock.read_text())["images"]["postgres"]
        image_id = run(DOCKER + ["image", "inspect", image["resolved"], "--format", "{{.Id}}"], "image.log").stdout.strip()
        if image_id != image["id"]:
            raise RuntimeError("Pinned PostgreSQL image mismatch")
        report["image"] = image
        manifest = json.loads((ARCHIVE / "manifest.json").read_text())
        retained = json.loads((ARCHIVE / "retained-assets.json").read_text())
        java = [ARCHIVE / "bundle/sources/candidate/reversedb/tpcc" / f
                for f in ("StockLevelService.java", "Sql.java")]
        jars = [LIB / n for n in ("postgresql-42.7.4.jar", "json-20240303.jar")]
        for path in java:
            if digest(path) != manifest[str(path.relative_to(ARCHIVE))]:
                raise RuntimeError("Decompiled source mismatch")
        for path in jars:
            if digest(path) != retained[str(path)]:
                raise RuntimeError("Retained runtime JAR mismatch")
        harness = ROOT / "prototype/app_db_questions/DatabaseSmoke.java"
        inputs = [Path(__file__), harness, *java, *jars, lock,
                  ROOT / "docs/APP_DB_PROGRAM_BINDINGS.md", ROOT / "docs/APP_DB_THEORETICAL_MODEL.md",
                  ARCHIVE / "manifest.json", ARCHIVE / "retained-assets.json"]
        for path in inputs:
            relative = path.relative_to(ROOT)
            copied = out / "inputs" / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
            report["inputs"][str(relative)] = digest(copied)
        classes = out / "classes"
        classes.mkdir()
        cp = ":".join(str(out / "inputs" / p.relative_to(ROOT)) for p in jars)
        run([JDK / "javac", "--release", "23", "-cp", cp, "-d", classes,
             *[out / "inputs" / p.relative_to(ROOT) for p in [*java, harness]]], "compile.log")
        report["java_version"] = run([JDK / "java", "-version"], "java-version.log").stdout.strip()
        # Database storage is tmpfs; no user directories or existing volumes mounted.
        result = run(DOCKER + ["run", "-d", "--pull=never", "--name", name,
                  "--label", "reversedb.experiment=app-db-smoke", "--memory=384m", "--cpus=1",
                  "--tmpfs", "/var/lib/postgresql:rw,size=256m", "-p", "127.0.0.1::5432",
                  "-e", "POSTGRES_USER=research", "-e", "POSTGRES_PASSWORD=local-fixture-only",
                  "-e", "POSTGRES_DB=research", image["resolved"], "-c", "timezone=UTC"], "start.log")
        created = True
        report["container_id"] = result.stdout.strip()
        for attempt in range(30):
            ready = run(DOCKER + ["exec", name, "pg_isready", "-U", "research", "-d", "research"],
                        f"ready-{attempt}.log", check=False, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Database not ready")
        port = run(DOCKER + ["port", name, "5432/tcp"], "port.log").stdout.strip()
        if not port.startswith("127.0.0.1:") or "\n" in port:
            raise RuntimeError("Unexpected port exposure")
        url = f"jdbc:postgresql://{port}/research?sslmode=disable"
        run([JDK / "java", "-Xmx128m", "-cp", str(classes) + ":" + cp,
             "reversedb.tpcc.DatabaseSmoke", url, out / "probe.json"], "probe.log", timeout=60)
        probe = json.loads((out / "probe.json").read_text())
        expected = dict(scalar_differential=11, page_four_arm=18, mutant=7, consumer_admission=4)
        if probe["status"] != "passed" or probe["totals"] != expected:
            raise RuntimeError("Unexpected experiment result/coverage")
        report["totals"] = probe["totals"]
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = repr(error)
        raise
    finally:
        if created:
            for command, log in [(DOCKER + ["logs", name], "postgres.log"),
                                 (DOCKER + ["rm", "-f", "-v", name], "cleanup.log")]:
                try:
                    result = run(command, log, check=False)
                    if result.returncode:
                        report["cleanup_errors"].append(log)
                except Exception as error:
                    report["cleanup_errors"].append(repr(error))
        if report["cleanup_errors"]:
            report["status"] = "failed"
        (out / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        files = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob("*")) if p.is_file()}
        (out / "manifest.json").write_text(json.dumps(files, indent=2) + "\n")
        print(json.dumps(dict(status=report["status"], output=str(out), cleanup_errors=report["cleanup_errors"])))
    if report["status"] != "passed":
        raise RuntimeError("Experiment did not finish cleanly")


if __name__ == "__main__":
    main()
