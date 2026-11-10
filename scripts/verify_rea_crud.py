#!/usr/bin/env python3
"""Start a private fresh REA replica, check new-object CRUD, and stop it."""
import argparse
import json
import socket
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-modules", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--har", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = args.bundle / "envs/paperless-tags-crud"
    command = [str(args.node), str(args.node_modules / "rea-agents/scripts/rea.mjs"),
               "environment-serve", str(env / "manifest.json"), "--evidence",
               str(env / "evidence.json"), "--host", "127.0.0.1", "--port", str(port)]
    record = {"server_command": command, "status": "running"}
    with (args.output / "server.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError("REA server exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("REA startup exceeded 30s")
                    time.sleep(0.1)
            probe = [str(args.node), str(Path(__file__).with_name("rea_crud_probe.mjs")),
                     str(args.node_modules / "@modelcontextprotocol/client/dist/index.mjs"),
                     f"http://127.0.0.1:{port}/mcp", str(args.har), str(args.output / "probe.json")]
            record["probe_command"] = probe
            with (args.output / "client.log").open("w") as client_log:
                result = subprocess.run(probe, stdout=client_log, stderr=subprocess.STDOUT, timeout=45)
            record.update(probe_exit_code=result.returncode,
                          status="passed" if result.returncode == 0 else "failed")
        except BaseException as exc:
            record.update(status="failed", error=str(exc))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            record.update(server_stopped=process.poll() is not None, server_exit_code=process.returncode)
            (args.output / "lifecycle.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": record["status"], "server_stopped": record["server_stopped"],
                      "evidence": str(args.output)}))
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
