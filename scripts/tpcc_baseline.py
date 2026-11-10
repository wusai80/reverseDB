#!/usr/bin/env python3
"""Run the pinned BenchBase build against a fresh local PostgreSQL container."""
import hashlib
import json
import os
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work/tpcc"
DOCKER = ["docker", "--context", "desktop-linux"]
COMMIT = "33c00473807ebd49304d114a6d769d2d2b2bbb34"


def text(command, timeout=30):
    return subprocess.check_output(command, text=True, timeout=timeout).strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    endpoint = text(DOCKER + ["context", "inspect", "desktop-linux", "--format", "{{.Endpoints.docker.Host}}"])
    if not endpoint.startswith("unix://"):
        raise RuntimeError("Only the local Docker Unix socket is allowed")
    source = json.loads((WORK / "source-provenance.json").read_text())
    if source["commit"] != COMMIT:
        raise RuntimeError("Unexpected BenchBase source version")
    image = json.loads((ROOT / "work/paperless/images.lock.json").read_text())["images"]["postgres"]
    inspected = json.loads(text(DOCKER + ["image", "inspect", image["resolved"], "--format", "{{json .}} "]))
    if inspected["Id"] != image["id"]:
        raise RuntimeError("PostgreSQL image differs from recorded digest")
    suffix = uuid.uuid4().hex[:12]
    name = "reversedb-tpcc-" + suffix
    out = WORK / "runs" / suffix
    out.mkdir(parents=True, exist_ok=False)
    dist = out / "distribution"
    archive = WORK / "source/target/benchbase-postgres.tgz"
    dist.mkdir()
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or ".." in parts or member.issym() or member.islnk():
                raise RuntimeError("Unexpected package member")
            dest = dist / member.name
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(package.extractfile(member).read())
    dist = dist / "benchbase-postgres"
    java = ["java", "-Xmx1024m", "-XX:ActiveProcessorCount=2"]
    cp = str(dist / "benchbase.jar") + os.pathsep + str(dist / "lib/*")
    classes = out / "probe-classes"
    classes.mkdir()
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "source": source,
              "name": name, "status": "running", "postgres": image,
              "distribution_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr,
              "scope": "1 warehouse, 4 terminals, 30 seconds at configured total rate 10; equal transaction weights for functional coverage, not certified TPC-C or performance measurement.",
              "commands": [], "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [Path(__file__), ROOT / "prototype/tpcc/TpccAtomicityProbe.java", ROOT / "prototype/tpcc/invariants.sql"]}}
    save(out / "run.json", record)

    def run(command, log, timeout=120, binary=False, input_text=None):
        record["commands"].append(command)
        print(log, flush=True)
        with (out / log).open("wb" if binary else "w") as stream:
            subprocess.run(command, cwd=dist, stdout=stream, stderr=None if binary else subprocess.STDOUT,
                           text=not binary, input=input_text, check=True, timeout=timeout)

    def sql(query):
        return text(DOCKER + ["exec", name, "psql", "-U", "tpcc", "-d", "tpcc", "-XAt", "-v", "ON_ERROR_STOP=1", "-c", query], timeout=60)

    def counts():
        tables = ["warehouse", "district", "customer", "item", "stock", "oorder", "new_order", "order_line", "history"]
        return {table: int(sql("SELECT count(*) FROM " + table)) for table in tables}

    try:
        run(["javac", "--release", "23", "-cp", cp, "-d", str(classes),
             str(ROOT / "prototype/tpcc/TpccAtomicityProbe.java")], "compile-probe.log")
        # A unique bridge plus loopback-only publishing lets the host JVM connect.
        # Desktop did not publish the requested port on an internal network.
        run(DOCKER + ["network", "create", "--label", "reversedb.run=" + name, name], "network-create.log")
        run(DOCKER + ["volume", "create", "--label", "reversedb.run=" + name, name], "volume-create.log")
        run(DOCKER + ["run", "-d", "--pull=never", "--name", name, "--label", "reversedb.run=" + name,
                      "--network", name, "--cpus=2", "--memory=1536m", "--shm-size=128m",
                      "-p", "127.0.0.1::5432", "--mount", "type=volume,src=" + name + ",dst=/var/lib/postgresql",
                      "-e", "POSTGRES_USER=tpcc", "-e", "POSTGRES_PASSWORD=synthetic-tpcc-only",
                      "-e", "POSTGRES_DB=tpcc", image["resolved"], "-c", "shared_buffers=256MB",
                      "-c", "log_line_prefix=%m session=%c pid=%p tx=%x app=%a db=%d ",
                      "-c", "log_connections=on", "-c", "log_disconnections=on"], "database-start.log")
        deadline = time.monotonic() + 60
        while subprocess.run(DOCKER + ["exec", name, "pg_isready", "-U", "tpcc"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            if time.monotonic() >= deadline:
                raise TimeoutError("Database did not become ready")
            time.sleep(0.5)
        save(out / "container.json", json.loads(text(DOCKER + ["inspect", name])))
        port = text(DOCKER + ["port", name, "5432/tcp"])
        if not port.startswith("127.0.0.1:"):
            raise RuntimeError("Database must bind to loopback only")
        url = f"jdbc:postgresql://{port}/tpcc?sslmode=disable&ApplicationName=tpcc-functional&reWriteBatchedInserts=true"
        config = ET.parse(dist / "config/postgres/sample_tpcc_config.xml")
        root = config.getroot()
        for key, value in {"url": url, "username": "tpcc", "password": "synthetic-tpcc-only", "terminals": "4",
                           "scalefactor": "1", "loaderThreads": "2", "randomSeed": "42"}.items():
            child = root.find(key)
            if child is None:
                child = ET.SubElement(root, key)
            child.text = value
        for key, value in {"time": "30", "rate": "10", "weights": "20,20,20,20,20"}.items():
            root.find("works/work/" + key).text = value
        config_path = out / "tpcc.xml"
        config.write(config_path, encoding="utf-8", xml_declaration=True)
        base = java + ["-jar", str(dist / "benchbase.jar"), "-b", "tpcc", "-c", str(config_path)]
        run(base + ["--create=true", "--load=true", "--execute=false"], "load.log", timeout=300)
        save(out / "counts-loaded.json", counts())
        run(DOCKER + ["exec", name, "pg_dump", "-U", "tpcc", "-d", "tpcc", "-Fc"], "initial.dump", binary=True)
        # No verbose SQL logging while loading the large fixture.
        sql("ALTER SYSTEM SET log_statement='all'")
        sql("SELECT pg_reload_conf()")
        run(java + ["-cp", str(classes) + os.pathsep + cp, "com.oltpbenchmark.api.TpccAtomicityProbe",
                    url, "tpcc", "synthetic-tpcc-only", str(out / "atomicity.json")], "atomicity.log")
        before = json.loads(sql((ROOT / "prototype/tpcc/invariants.sql").read_text()))
        save(out / "invariants-before-workload.json", before)
        if any(before.values()):
            raise RuntimeError("Invariant failed before workload")
        results = out / "results"
        results.mkdir()
        run(base + ["--create=false", "--load=false", "--execute=true", "-d", str(results),
                    "-jh", str(out / "histograms.json")], "workload.log", timeout=120)
        after = json.loads(sql((ROOT / "prototype/tpcc/invariants.sql").read_text()))
        save(out / "invariants-after-workload.json", after)
        save(out / "counts-final.json", counts())
        if any(after.values()):
            raise RuntimeError("Invariant failed after workload")
        record["status"] = "execution_passed_pending_coverage_review"
    except BaseException as error:
        record.update(status="failed", error=str(error))
    finally:
        # Preserve server output even when compilation, loading or assertions failed.
        cleanup_errors = []
        for log, cmd in [
            ("postgres.log", DOCKER + ["logs", name]),
            ("container-remove.log", DOCKER + ["rm", "-f", name]),
            ("volume-remove.log", DOCKER + ["volume", "rm", name]),
            ("network-remove.log", DOCKER + ["network", "rm", name]),
        ]:
            try:
                run(cmd, log, timeout=45)
            except Exception as error:
                cleanup_errors.append({"step": log, "error": str(error)})
        record["cleanup_errors"] = cleanup_errors
        record["remaining"] = {kind: text(DOCKER + [kind, "ls", "-q", "--filter", "label=reversedb.run=" + name])
                               for kind in ["container", "volume", "network"]}
        if any(record["remaining"].values()) or cleanup_errors:
            record["status"] = "failed"
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(out / "run.json", record)
        print(json.dumps({"status": record["status"], "evidence": str(out)}), flush=True)
    if record["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
