#!/usr/bin/env python3
"""Run the HTTP probe in a fresh internal Compose project atop the locked B0 image."""
import hashlib
import argparse
import json
import os
import uuid
from pathlib import Path

import paperless_baseline as b0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=("b1", "crud", "permissions"), default="b1")
    args = parser.parse_args()
    prefix = b0.require_ready()
    lock = b0.load_lock()
    built = b0.load_build(prefix, lock)
    harness = b0.ROOT / "prototype/rea_http"
    hashes = {str(p.relative_to(b0.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(harness.iterdir()) if p.is_file()}
    hashes["scripts/paperless_http_probe.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    suffix = uuid.uuid4().hex[:12]
    project = "reversedb-http-" + suffix
    container = project + "-app"
    destination = b0.ROOT / "work/paperless-http" / suffix
    destination.mkdir(parents=True)
    record = {"started_at": b0.now(), "project": project, "inputs": hashes, "experiment": args.experiment,
              "base_build": built, "status": "running"}
    b0.write_json(destination/"run.json", record)
    base_tag = "reversedb-paperless-b0:" + lock["input_fingerprint"]["sha256"][:16]
    if b0.image_info(prefix, base_tag)["id"] != built["app"]["id"]:
        raise RuntimeError("Base tag no longer matches the frozen image")
    tag = project + ":probe"
    b0.run_logged(prefix + ["build", "--pull=false", "--network=none", "--tag", tag,
                           "--build-arg", "BASE_IMAGE=" + base_tag, str(harness)], destination/"build.log")
    image = b0.image_info(prefix, tag)
    record["probe_image"] = image
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "RDB_"))}
    env.update(RDB_POSTGRES_IMAGE=lock["images"]["postgres"]["resolved"],
               RDB_BROKER_IMAGE=lock["images"]["broker"]["resolved"], RDB_APP_IMAGE=image["id"])
    compose = prefix + ["compose", "--env-file", "/dev/null", "-p", project,
                        "-f", str(b0.HARNESS/"compose.yaml")]
    try:
        b0.run_logged(compose+["config"], destination/"config.log", env=env)
        b0.run_logged(compose+["up", "-d", "db", "broker"], destination/"services.log", env=env, timeout=90)
        b0.wait_services(prefix, project, destination/"health.json")
        invocation = ["run", "--name", container, "--no-deps", "-T", "app"]
        if args.experiment == "crud":
            invocation += ["sh", "/http-harness/run_crud.sh"]
        elif args.experiment == "permissions":
            invocation += ["sh", "/http-harness/run_permissions.sh"]
        b0.run_logged(compose+invocation,
                      destination/"tests.log", env=env, timeout=600)
        record["status"] = "passed"
    except BaseException as exc:
        record.update(status="failed", error=str(exc))
    finally:
        for name, command in [
            ("artifacts", prefix+["cp", container+":/results/.", str(destination)]),
            ("app_cleanup", prefix+["rm", "--force", container]),
            ("cleanup", compose+["down", "--volumes", "--timeout", "10"]),
        ]:
            try:
                b0.run_logged(command, destination/(name+".log"), env=env, timeout=60)
                record[name] = "passed"
            except Exception as exc:
                record[name] = str(exc)
                record["status"] = "failed"
        required = ("crud.har", "crud-observations.json", "crud.xml") if args.experiment == "crud" else ("http.har", "server-trace.json", "http.xml", "b1-http.har",
                     "b1-server-trace.json", "b1-results.json", "b1-fault-counterexample.json",
                     "b1-drf-source.txt", "b1-upstream.xml")
        if args.experiment == "permissions":
            required = ("permissions.json", "permissions.xml", "permissions-owner.har",
                        "permissions-peer.har", "permissions-stranger.har",
                        "permissions-no_model.har", "permissions-anonymous.har")
        for name in required:
            if not (destination/name).is_file():
                record.update(status="failed", missing_artifact=name)
        # Independently check only this run's containers, volumes and network.
        remaining = {}
        for resource in ("container", "volume", "network"):
            remaining[resource] = b0.output(prefix+[resource, "ls", "-q", "--filter",
                                                       "label=com.docker.compose.project="+project])
        record["remaining_resources"] = remaining
        if any(remaining.values()):
            record["status"] = "failed"
        record["finished_at"] = b0.now()
        b0.write_json(destination/"run.json", record)
        print("Evidence: " + str(destination), flush=True)
    if record["status"] != "passed":
        raise SystemExit("HTTP probe failed; inspect the preserved logs")


if __name__ == "__main__":
    main()
