#!/usr/bin/env python3
"""Preflight, lock, build and run the isolated Paperless B0 experiment.

Uses Python's standard library on the host; all application dependencies and
databases live in Docker. No host ports, existing databases or source edits.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from checkout_sources import ROOT, verify

HARNESS = ROOT / "prototype" / "paperless"
WORK = ROOT / "work" / "paperless"
DIGEST = re.compile(r"^[\w./:-]+@sha256:[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def capture(command, timeout=15):
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return {"command": command, "exit_code": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "exit_code": None, "stdout": "", "stderr": str(exc)}


def output(command):
    result = capture(command)
    if result["exit_code"] != 0:
        raise RuntimeError(result["stderr"] or f"Command failed: {command}")
    return result["stdout"]


def fingerprint():
    paths = [ROOT / "sources.lock.json", ROOT / ".dockerignore", Path(__file__),
             ROOT / "sources/paperless-ngx/pyproject.toml", ROOT / "sources/paperless-ngx/uv.lock"]
    paths.extend(p for p in HARNESS.iterdir() if p.is_file())
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(paths)}
    return {"sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            "files": hashes}


def doctor():
    checks = []
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    for repo in lock["repositories"]:
        try:
            verify(ROOT / "sources" / repo["name"], repo)
            checks.append({"check": f"source:{repo['name']}", "ok": True, "commit": repo["commit"]})
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            checks.append({"check": f"source:{repo['name']}", "ok": False, "detail": str(exc)})
    evidence = capture([sys.executable, str(ROOT / "scripts/inspect_regions.py")])
    checks.append({"check": "static_evidence", "ok": evidence["exit_code"] == 0, **evidence})
    context_result = capture(["docker", "context", "show"])
    context = context_result["stdout"] if context_result["exit_code"] == 0 else None
    endpoint = capture(["docker", "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"]
                       ) if context else context_result
    local = endpoint["exit_code"] == 0 and endpoint["stdout"].startswith("unix://")
    checks.append({"check": "local_docker_context", "ok": local, **endpoint})
    prefix = ["docker", "--context", context] if context else ["docker"]
    # Never probe or mutate a remote daemon, even if the user's default is remote.
    if local:
        server = capture(prefix + ["version", "--format", "{{json .Server}}"])
        checks.append({"check": "docker_server", "ok": server["exit_code"] == 0, **server})
    else:
        checks.append({"check": "docker_server", "ok": False, "detail": "Local Unix-socket Docker context required"})
    compose = capture(prefix + ["compose", "version", "--short"])
    help_result = capture(prefix + ["compose", "config", "--help"])
    supported = compose["exit_code"] == 0 and all(
        flag in help_result["stdout"] for flag in ("--format", "--no-interpolate")
    )
    checks.append({"check": "compose_cli", "ok": supported, **compose,
                   "required": "Compose config JSON support; readiness is polled without --wait"})
    return {
        "schema_version": 1, "checked_at": now(), "scope": "Local preflight only; no application tests or benchmark",
        "status": "ready" if all(c["ok"] for c in checks) else "blocked",
        "host_python": sys.version.split()[0], "docker_context": context,
        "checks": checks, "input_fingerprint": fingerprint(),
    }


def require_ready():
    report = doctor()
    if report["status"] != "ready":
        failed = ", ".join(c["check"] for c in report["checks"] if not c["ok"])
        raise RuntimeError(f"Preflight blocked: {failed}. Run doctor --write for details.")
    if WORK.is_symlink() or not WORK.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Work directory must stay inside this project")
    WORK.mkdir(parents=True, exist_ok=True)
    return ["docker", "--context", report["docker_context"]]


def run_logged(command, log, *, env=None, timeout=3600):
    print(f"Running: {' '.join(command)}\nLog: {log}", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                env=env, timeout=timeout)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)


def image_info(prefix, image):
    result = json.loads(output(prefix + ["image", "inspect", image, "--format",
                                       '{{json .}}']))
    return {"id": result["Id"], "architecture": result["Architecture"],
            "os": result["Os"], "repo_digests": result.get("RepoDigests") or []}


def image_repository(reference):
    repository = reference.split("@", 1)[0]
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    first = repository.split("/", 1)[0]
    if "/" not in repository or not any(c in first for c in (".", ":")) and first != "localhost":
        repository = "docker.io/" + repository
    if repository.startswith("docker.io/") and "/" not in repository[len("docker.io/"):]:
        repository = "docker.io/library/" + repository[len("docker.io/"):]
    return repository


def validate_lock(lock, current):
    if lock["input_fingerprint"] != current:
        raise RuntimeError("Experiment inputs changed; preserve the previous lock/run and lock a new environment")
    expected = json.loads((HARNESS / "images.json").read_text())["images"]
    if set(lock["images"]) != set(expected):
        raise RuntimeError("Incomplete image lock")
    for name, item in lock["images"].items():
        if item["requested"] != expected[name] or not DIGEST.fullmatch(item["resolved"]):
            raise RuntimeError(f"Unpinned or unexpected image: {name}")
        if image_repository(item["resolved"]) != image_repository(expected[name]):
            raise RuntimeError(f"Wrong image repository: {name}")
        if "@" in expected[name] and item["resolved"].split("@", 1)[1] != expected[name].split("@", 1)[1]:
            raise RuntimeError(f"Pinned digest changed: {name}")
        if not IMAGE_ID.fullmatch(item["id"]) or item["os"] != "linux":
            raise RuntimeError(f"Invalid Linux image identity: {name}")


def load_lock():
    path = WORK / "images.lock.json"
    if not path.exists():
        raise RuntimeError("Run lock-images first; service image digests are not frozen yet")
    lock = json.loads(path.read_text())
    validate_lock(lock, fingerprint())
    return lock


def lock_images(prefix):
    path = WORK / "images.lock.json"
    if path.exists():
        load_lock()
        print(f"Existing lock preserved: {path}")
        return
    spec = json.loads((HARNESS / "images.json").read_text())
    lock = {"schema_version": 1, "locked_at": now(), "input_fingerprint": fingerprint(), "images": {}}
    for name, requested in spec["images"].items():
        run_logged(prefix + ["pull", requested], WORK / f"pull-{name}.log", timeout=1800)
        info = image_info(prefix, requested)
        # Select only the digest for the requested repository, not another tag.
        repository = image_repository(requested)
        matches = [d for d in info["repo_digests"]
                   if image_repository(d) == repository]
        resolved = requested if "@sha256:" in requested else (matches[0] if matches else None)
        if resolved is None:
            raise RuntimeError(f"No repository digest for {requested}")
        lock["images"][name] = {"requested": requested, "resolved": resolved, **info}
    validate_lock(lock, fingerprint())
    write_json(path, lock)
    print(f"Locked: {path}")


def build(prefix):
    lock = load_lock()
    path = WORK / "build.lock.json"
    if path.exists():
        load_build(prefix, lock)
        print(f"Existing build preserved: {path}")
        return
    tag = f"reversedb-paperless-b0:{lock['input_fingerprint']['sha256'][:16]}"
    run_logged(prefix + ["build", "--file", str(HARNESS / "Dockerfile"), "--tag", tag,
                         "--build-arg", f"PYTHON_UV_IMAGE={lock['images']['python_uv']['resolved']}", str(ROOT)],
               WORK / "build.log")
    info = image_info(prefix, tag)
    write_json(path, {"schema_version": 1, "built_at": now(),
                      "input_fingerprint": fingerprint(), "images": lock["images"], "app": info})
    print(f"Built and frozen: {path}")


def load_build(prefix, lock):
    path = WORK / "build.lock.json"
    if not path.exists():
        raise RuntimeError("Run build first")
    record = json.loads(path.read_text())
    if record["input_fingerprint"] != fingerprint() or record["images"] != lock["images"]:
        raise RuntimeError("Build does not match the frozen inputs")
    if not IMAGE_ID.fullmatch(record["app"]["id"]):
        raise RuntimeError("Build image ID is not immutable")
    # Require all images on this daemon; never silently pull a mutable tag.
    for item in [record["app"], *lock["images"].values()]:
        actual = image_info(prefix, item.get("resolved", item["id"]))
        if any(actual[key] != item[key] for key in ("id", "architecture", "os")):
            raise RuntimeError("Local image differs from lock")
    return record


def wait_services(prefix, project, log, timeout=60):
    deadline = time.monotonic() + timeout
    probes = []
    while time.monotonic() < deadline:
        states = {}
        for service in ("db", "broker"):
            ids = output(prefix + ["ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={project}",
                                   "--filter", f"label=com.docker.compose.service={service}"]).splitlines()
            if len(ids) != 1:
                raise RuntimeError(f"Expected one isolated {service} container, got {len(ids)}")
            state = json.loads(output(prefix + ["inspect", "--format", "{{json .State}}", ids[0]]))
            states[service] = {"container": ids[0], "status": state["Status"],
                               "health": state.get("Health", {}).get("Status")}
        probes.append({"at": now(), "services": states})
        write_json(log, probes)
        if all(s["status"] == "running" and s["health"] == "healthy" for s in states.values()):
            return
        if any(s["status"] in ("exited", "dead") or s["health"] == "unhealthy" for s in states.values()):
            raise RuntimeError(f"Service unhealthy: see {log}")
        time.sleep(1)
    raise RuntimeError(f"Service readiness timed out: see {log}")


def run_tests(prefix):
    lock = load_lock()
    built = load_build(prefix, lock)
    suffix = uuid.uuid4().hex[:12]
    project = f"reversedb-b0-{suffix}"
    container = f"{project}-app"
    destination = WORK / "runs" / f"{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d-%H%M%S}-{suffix}"
    destination.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "RDB_"))}
    env.update({"RDB_POSTGRES_IMAGE": lock["images"]["postgres"]["resolved"],
                "RDB_BROKER_IMAGE": lock["images"]["broker"]["resolved"],
                "RDB_APP_IMAGE": built["app"]["id"]})
    command = prefix + ["compose", "--env-file", "/dev/null", "-p", project,
                        "-f", str(HARNESS / "compose.yaml")]
    record = {"schema_version": 1, "started_at": now(), "project": project,
              "images_lock": lock, "build_lock": built, "status": "running", "steps": []}
    write_json(destination / "run.json", record)
    try:
        for name, args in [("config", ["config"]),
                           ("services", ["up", "-d", "db", "broker"]),
                           ("tests", ["run", "--name", container, "--no-deps", "-T", "app"])]:
            step = {"name": name, "command": command + args, "exit_code": None}
            record["steps"].append(step)
            write_json(destination / "run.json", record)
            try:
                run_logged(step["command"], destination / f"{name}.log", env=env, timeout=1800)
            except subprocess.CalledProcessError as exc:
                step.update({"status": "failed; see log", "exit_code": exc.returncode})
                raise
            except Exception:
                step["status"] = "failed; see log"
                raise
            step.update({"status": "passed", "exit_code": 0})
            if name == "services":
                wait_services(prefix, project, destination / "health.json")
        record["status"] = "passed"
    except BaseException as exc:
        record.update({"status": "failed", "error": str(exc)})
        raise
    finally:
        # Docker-owned results avoid host-directory sharing/permission prompts.
        # Preserve partial JUnit and environment evidence even if pytest fails.
        try:
            run_logged(prefix + ["cp", f"{container}:/results/.", str(destination)],
                       destination / "artifacts.log", timeout=30)
            record["artifacts"] = "copied"
        except Exception as exc:
            record["artifacts"] = f"failed: {exc}"
            if record["status"] == "passed":
                record.update({"status": "failed", "error": "Could not copy all run artifacts"})
        if record["status"] == "passed" and not (destination / "b0-trace.json").is_file():
            record.update({"status": "failed", "error": "Tests exited successfully but trace artifact is missing"})
        # Compose down excludes one-off `run` containers; remove this exact
        # container after copying results so its volume can also be removed.
        try:
            run_logged(prefix + ["rm", "--force", container],
                       destination / "app-cleanup.log", timeout=30)
            record["app_cleanup"] = "passed"
        except Exception as exc:
            log = destination / "app-cleanup.log"
            if isinstance(exc, subprocess.CalledProcessError) and log.exists() and "No such container" in log.read_text():
                record["app_cleanup"] = "not_created_or_already_removed"
            else:
                record["app_cleanup"] = f"failed: {exc}"
        # Only the fresh UUID project generated above is ever cleaned up.
        try:
            run_logged(command + ["down", "--volumes", "--timeout", "10"],
                       destination / "cleanup.log", env=env, timeout=60)
            record["cleanup"] = "passed"
        except Exception as exc:
            record["cleanup"] = f"failed: {exc}"
        if record["app_cleanup"].startswith("failed:"):
            record["cleanup"] = record["app_cleanup"]
        record["finished_at"] = now()
        write_json(destination / "run.json", record)
        print(f"Run evidence: {destination}", flush=True)
    if record["cleanup"] != "passed":
        raise RuntimeError(f"Test run finished but isolated resources remain: {project}")
    if record["status"] != "passed":
        raise RuntimeError(record["error"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "lock-images", "build", "test"))
    parser.add_argument("--write", action="store_true", help="save doctor report to evidence/runtime-preflight.json")
    args = parser.parse_args()
    if args.write and args.action != "doctor":
        parser.error("--write is only supported by doctor")
    if args.action == "doctor":
        report = doctor()
        if args.write:
            write_json(ROOT / "evidence/runtime-preflight.json", report)
        for check in report["checks"]:
            print(f"{'PASS' if check['ok'] else 'BLOCKED'} {check['check']}")
        return 0 if report["status"] == "ready" else 2
    prefix = require_ready()
    {"lock-images": lock_images, "build": build, "test": run_tests}[args.action](prefix)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
