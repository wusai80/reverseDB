"""Host-only checks for isolation and instrumentation, not Paperless tests."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "prototype/paperless")]
import paperless_baseline as baseline
from trace_sql import SQLTrace


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.trace = SQLTrace()
        self.context = {"connection": SimpleNamespace(in_atomic_block=True, get_autocommit=lambda: False)}

    def test_preserves_result_arguments_and_does_not_log_parameter_values(self):
        params = ("private synthetic marker", 12)
        sentinel = object()
        seen = []

        def execute(*args):
            seen.append(args)
            return sentinel

        with self.trace.during("helper_build"):
            result = self.trace(execute, "SELECT %s, %s", params, False, self.context)
        self.assertIs(result, sentinel)
        self.assertIs(seen[0][1], params)
        record = self.trace.queries[0]
        self.assertEqual(record["parameter_types"], ["str", "int"])
        self.assertEqual(record["phase"], "helper_build")
        self.assertNotIn(params[0], json.dumps(record))
        self.assertGreaterEqual(record["execute_ns"], 0)

    def test_exception_propagates_and_nested_phase_is_restored(self):
        error = ValueError("private synthetic error message")

        def fail(*args):
            raise error

        with self.trace.during("outer"):
            with self.assertRaises(ValueError) as caught:
                with self.trace.during("inner"):
                    self.trace(fail, "SELECT %s", (1,), False, self.context)
            self.assertIs(caught.exception, error)
            self.assertEqual(self.trace.phase, "outer")
        self.assertEqual(self.trace.phase, "unlabelled")
        self.assertEqual(self.trace.queries[0]["error_type"], "ValueError")
        self.assertNotIn(str(error), json.dumps(self.trace.queries))

    def test_executemany_iterator_is_not_consumed_by_tracer(self):
        params = iter([(1,), (2,)])
        observed = []

        def execute(sql, actual, many, context):
            self.assertIs(actual, params)
            observed.extend(actual)

        self.trace(execute, "SELECT %s", params, True, self.context)
        self.assertEqual(observed, [(1,), (2,)])
        self.assertIsNone(self.trace.queries[0]["parameter_types"])


class LockTests(unittest.TestCase):
    def setUp(self):
        requested = json.loads((baseline.HARNESS / "images.json").read_text())["images"]
        self.fingerprint = {"sha256": "fixture", "files": {}}
        self.lock = {
            "input_fingerprint": self.fingerprint,
            "images": {name: {"requested": ref,
                              "resolved": ref if "@" in ref else baseline.image_repository(ref) + "@sha256:" + "a" * 64,
                              "id": "sha256:" + "b" * 64, "os": "linux"}
                       for name, ref in requested.items()},
        }

    def test_changed_inputs_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "inputs changed"):
            baseline.validate_lock(self.lock, {"sha256": "changed"})

    def test_mutable_image_rejected(self):
        self.lock["images"]["postgres"]["resolved"] = "postgres:18"
        with self.assertRaisesRegex(RuntimeError, "Unpinned"):
            baseline.validate_lock(self.lock, self.fingerprint)

    def test_missing_image_rejected(self):
        del self.lock["images"]["broker"]
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            baseline.validate_lock(self.lock, self.fingerprint)

    def test_non_linux_image_rejected(self):
        self.lock["images"]["broker"]["os"] = "windows"
        with self.assertRaisesRegex(RuntimeError, "Invalid Linux"):
            baseline.validate_lock(self.lock, self.fingerprint)

    def test_docker_hub_repository_aliases_match(self):
        self.assertEqual(baseline.image_repository("postgres@sha256:" + "a" * 64),
                         baseline.image_repository("docker.io/library/postgres:18"))
        baseline.validate_lock(self.lock, self.fingerprint)

    def test_wrong_repository_rejected_even_when_digest_is_pinned(self):
        self.lock["images"]["postgres"]["resolved"] = "unrelated/db@sha256:" + "a" * 64
        with self.assertRaisesRegex(RuntimeError, "Wrong image repository"):
            baseline.validate_lock(self.lock, self.fingerprint)

    def test_fixed_python_digest_cannot_be_replaced(self):
        self.lock["images"]["python_uv"]["resolved"] = "ghcr.io/astral-sh/uv@sha256:" + "a" * 64
        with self.assertRaisesRegex(RuntimeError, "Pinned digest changed"):
            baseline.validate_lock(self.lock, self.fingerprint)


class IsolationTests(unittest.TestCase):
    def test_preflight_never_contacts_remote_daemon(self):
        commands = []

        def capture(command, **kwargs):
            commands.append(command)
            stdout = ""
            if command[-2:] == ["context", "show"]:
                stdout = "remote-fixture"
            elif "inspect" in command:
                stdout = "tcp://example.invalid:2376"
            elif "--help" in command:
                stdout = "--format --no-interpolate"
            return {"command": command, "stdout": stdout, "stderr": "", "exit_code": 0}

        with patch.object(baseline, "capture", side_effect=capture), patch.object(baseline, "verify"):
            report = baseline.doctor()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(any(".Server" in " ".join(c) for c in commands))

    def test_blocked_preflight_prevents_mutation(self):
        with patch.object(baseline, "doctor", return_value={
            "status": "blocked", "checks": [{"check": "docker_server", "ok": False}],
        }), patch.object(baseline, "run_logged") as run:
            with self.assertRaisesRegex(RuntimeError, "Preflight blocked"):
                baseline.require_ready()
            run.assert_not_called()

    def test_test_failure_records_exit_code_and_cleans_only_new_project(self):
        lock = {"images": {"postgres": {"resolved": "fixture-postgres"},
                           "broker": {"resolved": "fixture-broker"}}}
        calls = []

        def run(command, log, **kwargs):
            calls.append(command)
            if log.name == "tests.log":
                raise subprocess.CalledProcessError(7, command)

        with tempfile.TemporaryDirectory(prefix="reversedb-unit-") as temp:
            with patch.object(baseline, "WORK", Path(temp)), \
                 patch.object(baseline, "load_lock", return_value=lock), \
                 patch.object(baseline, "load_build", return_value={"app": {"id": "fixture-app"}}), \
                 patch.object(baseline, "wait_services"), \
                 patch.object(baseline, "run_logged", side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    baseline.run_tests(["docker", "--context", "fixture"])
            record = json.loads(next(Path(temp).glob("runs/*/run.json")).read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["steps"][-1]["exit_code"], 7)
        self.assertEqual(record["cleanup"], "passed")
        self.assertRegex(record["project"], r"^reversedb-b0-[0-9a-f]{12}$")
        self.assertIn("down", calls[-1])
        self.assertIn("--volumes", calls[-1])
        self.assertTrue(all(c[c.index("-p") + 1] == record["project"] for c in calls if "compose" in c))
        copy = next(c for c in calls if "cp" in c)
        self.assertEqual(copy[copy.index("cp") + 1], f"{record['project']}-app:/results/.")
        self.assertEqual(record["artifacts"], "copied")
        self.assertLess(calls.index(copy), len(calls) - 1)
        remove = next(c for c in calls if "rm" in c)
        self.assertEqual(remove[-1], f"{record['project']}-app")
        self.assertLess(calls.index(copy), calls.index(remove))
        self.assertLess(calls.index(remove), len(calls) - 1)
        self.assertEqual(record["app_cleanup"], "passed")

    def test_missing_trace_cannot_be_reported_as_a_pass(self):
        lock = {"images": {"postgres": {"resolved": "fixture-postgres"},
                           "broker": {"resolved": "fixture-broker"}}}
        with tempfile.TemporaryDirectory(prefix="reversedb-missing-trace-") as temp:
            with patch.object(baseline, "WORK", Path(temp)), \
                 patch.object(baseline, "load_lock", return_value=lock), \
                 patch.object(baseline, "load_build", return_value={"app": {"id": "fixture-app"}}), \
                 patch.object(baseline, "wait_services"), \
                 patch.object(baseline, "run_logged"):
                with self.assertRaisesRegex(RuntimeError, "trace artifact is missing"):
                    baseline.run_tests(["docker", "--context", "fixture"])
            record = json.loads(next(Path(temp).glob("runs/*/run.json")).read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["cleanup"], "passed")

    def test_partial_artifact_copy_cannot_be_reported_as_a_pass(self):
        lock = {"images": {"postgres": {"resolved": "fixture-postgres"},
                           "broker": {"resolved": "fixture-broker"}}}

        def run(command, log, **kwargs):
            if "cp" in command:
                (Path(command[-1]) / "b0-trace.json").write_text("{}")
                raise subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory(prefix="reversedb-partial-copy-") as temp:
            with patch.object(baseline, "WORK", Path(temp)), \
                 patch.object(baseline, "load_lock", return_value=lock), \
                 patch.object(baseline, "load_build", return_value={"app": {"id": "fixture-app"}}), \
                 patch.object(baseline, "wait_services"), \
                 patch.object(baseline, "run_logged", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "Could not copy all run artifacts"):
                    baseline.run_tests(["docker", "--context", "fixture"])
            record = json.loads(next(Path(temp).glob("runs/*/run.json")).read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["cleanup"], "passed")

    def test_readiness_rejects_crashed_service(self):
        def output(command):
            if "inspect" in command:
                return json.dumps({"Status": "exited", "Health": {"Status": "starting"}})
            return "fixture-container"

        with tempfile.TemporaryDirectory(prefix="reversedb-health-") as temp:
            with patch.object(baseline, "output", side_effect=output):
                with self.assertRaisesRegex(RuntimeError, "Service unhealthy"):
                    baseline.wait_services(["docker"], "reversedb-fixture", Path(temp) / "health.json")

    def test_compose_has_no_host_ports_or_external_volumes(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "RDB_"))}
        # Deliberately fake digests for parser-only validation; never pulled/run.
        env.update({"RDB_POSTGRES_IMAGE": "postgres@sha256:" + "a" * 64,
                    "RDB_BROKER_IMAGE": "valkey/valkey@sha256:" + "b" * 64,
                    "RDB_APP_IMAGE": "sha256:" + "c" * 64})
        try:
            result = subprocess.run(
                ["docker", "compose", "--env-file", "/dev/null", "-p", "reversedb-parser-only",
                 "-f", str(baseline.HARNESS / "compose.yaml"), "config", "--format", "json"],
                env=env, capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError:
            self.skipTest("Docker CLI unavailable; parser validation needs Compose")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertTrue(config["networks"]["default"]["internal"])
        self.assertFalse(config["volumes"]["postgres"].get("external"))
        self.assertFalse(config["volumes"]["results"].get("external"))
        for service in config["services"].values():
            self.assertNotIn("ports", service)
            self.assertNotIn("container_name", service)
            self.assertNotIn("network_mode", service)
            self.assertEqual(service["pull_policy"], "never")
        mounts = config["services"]["app"]["volumes"]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]["type"], "volume")
        self.assertEqual(mounts[0]["source"], "results")
        self.assertEqual(mounts[0]["target"], "/results")


if __name__ == "__main__":
    unittest.main()
