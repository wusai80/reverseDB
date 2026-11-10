#!/usr/bin/env python3
"""Run the preregistered finite cost matrix once; preserve all evidence."""
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app_db_smoke import ROOT, DOCKER, JDK, ARCHIVE, LIB, digest
from app_db_cost_proxy import CountingProxy


def main():
    out = ROOT / "evidence/app-db-costs" / ("20261104-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    name = "reversedb-costs-" + out.name
    report = dict(status="running", container=name, commands=[], inputs={}, cleanup_errors=[])
    created = False
    proxy = None

    def run(args, log, check=True, timeout=60):
        report["commands"].append([str(a) for a in args])
        result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        (out / log).write_text(result.stdout)
        if check and result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}), see {out / log}")
        return result

    try:
        protocol_path = ROOT / "prototype/app_db_questions/cost_protocol.json"
        protocol = json.loads(protocol_path.read_text())
        report["protocol_sha256"] = digest(protocol_path)
        lock = ROOT / "work/paperless/images.lock.json"
        image = json.loads(lock.read_text())["images"]["postgres"]
        inspected = run(DOCKER + ["image", "inspect", image["resolved"], "--format", "{{.Id}}"], "image.log").stdout.strip()
        if inspected != image["id"]:
            raise RuntimeError("PostgreSQL image mismatch")
        report["image"] = image
        smoke = ROOT / "evidence/app-db-smoke/20261104-2da99cd7f3cf"
        smoke_run = json.loads((smoke / "run.json").read_text())
        harness = ROOT / "prototype/app_db_questions/DatabaseSmoke.java"
        if smoke_run["status"] != "passed" or digest(harness) != smoke_run["inputs"][str(harness.relative_to(ROOT))]:
            raise RuntimeError("Validated four-arm fixture changed")
        manifest = json.loads((ARCHIVE / "manifest.json").read_text())
        retained = json.loads((ARCHIVE / "retained-assets.json").read_text())
        java = [ARCHIVE / "bundle/sources/candidate/reversedb/tpcc" / name for name in ("StockLevelService.java", "Sql.java")]
        jars = [LIB / name for name in ("postgresql-42.7.4.jar", "json-20240303.jar")]
        for path in java:
            if digest(path) != manifest[str(path.relative_to(ARCHIVE))]:
                raise RuntimeError("Decompiled dependency changed")
        for path in jars:
            if digest(path) != retained[str(path)]:
                raise RuntimeError("JDBC/JSON dependency changed")
        costs = ROOT / "prototype/app_db_questions/DatabaseCosts.java"
        inputs = [Path(__file__), ROOT / "scripts/app_db_smoke.py", ROOT / "scripts/app_db_cost_proxy.py",
                  costs, harness, protocol_path, lock, *java, *jars,
                  smoke / "run.json", smoke / "manifest.json", ARCHIVE / "manifest.json", ARCHIVE / "retained-assets.json",
                  ROOT / "prototype/app_db_questions/validation_spec.json"]
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
             *[out / "inputs" / p.relative_to(ROOT) for p in [*java, harness, costs]]], "compile.log")
        report["java_version"] = run([JDK / "java", "-version"], "java-version.log").stdout.strip()
        result = run(DOCKER + ["run", "-d", "--pull=never", "--name", name,
                    "--label", "reversedb.experiment=app-db-costs", "--memory=384m", "--cpus=1",
                    "--tmpfs", "/var/lib/postgresql:rw,size=256m", "-p", "127.0.0.1::5432",
                    "-e", "POSTGRES_USER=research", "-e", "POSTGRES_PASSWORD=local-fixture-only",
                    "-e", "POSTGRES_DB=research", image["resolved"], "-c", "timezone=UTC"], "start.log")
        created = True
        report["container_id"] = result.stdout.strip()
        for attempt in range(30):
            if run(DOCKER + ["exec", name, "pg_isready", "-U", "research", "-d", "research"],
                   f"ready-{attempt}.log", check=False, timeout=5).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Database readiness timeout")
        address = run(DOCKER + ["port", name, "5432/tcp"], "port.log").stdout.strip()
        if not address.startswith("127.0.0.1:") or "\n" in address:
            raise RuntimeError("Unexpected exposed address")
        host, port = address.split(":")
        proxy = CountingProxy((host, int(port)))
        suffix = "/research?sslmode=disable&gssEncMode=disable&prepareThreshold=0&reWriteBatchedInserts=true"
        command = [JDK / "java", "-Xms64m", "-Xmx256m", "-XX:+UseSerialGC", "-cp", str(classes) + ":" + cp,
                   "reversedb.tpcc.DatabaseCosts", "jdbc:postgresql://" + address + suffix,
                   "jdbc:postgresql://" + proxy.address + suffix, proxy.counter_url, out / "raw.json",
                   out / "inputs" / protocol_path.relative_to(ROOT)]
        report["commands"].append([str(a) for a in command])
        with (out / "probe.log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 600
            last = ""
            while process.poll() is None:
                lines = (out / "probe.log").read_text().splitlines()
                if lines and lines[-1] != last:
                    last = lines[-1]
                    print(last, flush=True)
                if time.monotonic() > deadline:
                    process.kill()
                    process.wait()
                    raise RuntimeError("Fixed experiment deadline exceeded")
                time.sleep(1)
            if process.returncode:
                raise RuntimeError(f"Java experiment failed; see {out / 'probe.log'}")
        raw = json.loads((out / "raw.json").read_text())
        if raw["status"] != "passed" or raw["timed_samples"] != protocol["expected_timed_samples"] or raw["diagnostic_calls"] != protocol["expected_diagnostic_calls"]:
            raise RuntimeError("Incomplete measurement matrix")
        report.update(status="passed", timed_samples=raw["timed_samples"], diagnostic_calls=raw["diagnostic_calls"])
    except BaseException as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        if proxy:
            try:
                proxy.close()
            except Exception as error:
                report["cleanup_errors"].append(repr(error))
        if created:
            for command, log in [(DOCKER + ["logs", name], "postgres.log"), (DOCKER + ["rm", "-f", "-v", name], "cleanup.log")]:
                try:
                    if run(command, log, check=False).returncode:
                        report["cleanup_errors"].append(log)
                except Exception as error:
                    report["cleanup_errors"].append(repr(error))
        if report["cleanup_errors"]:
            report["status"] = "failed"
        (out / "run.json").write_text(json.dumps(report, indent=2) + "\n")
        files = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob("*")) if p.is_file()}
        (out / "manifest.json").write_text(json.dumps(files, indent=2) + "\n")
        print(json.dumps(dict(status=report["status"], output=str(out), cleanup_errors=report["cleanup_errors"])), flush=True)
    if report["status"] != "passed":
        raise RuntimeError("Experiment did not complete cleanly")


if __name__ == "__main__":
    main()
