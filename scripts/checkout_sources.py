#!/usr/bin/env python3
"""Fetch exact commits; never reset or modify an existing checkout."""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True
    ).strip()


def verify(path, repo):
    if path.is_symlink() or not (path / ".git").is_dir():
        raise RuntimeError(f"Refusing non-checkout or symlink: {path}")
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise RuntimeError(f"Not an independent checkout: {path}")
    if git(path, "remote", "get-url", "origin") != repo["url"]:
        raise RuntimeError(f"Unexpected origin: {path}")
    if git(path, "rev-parse", "HEAD") != repo["commit"]:
        raise RuntimeError(f"Commit mismatch: {path}; existing files left untouched")
    if git(path, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(f"Dirty checkout: {path}; existing files left untouched")
    if subprocess.run(["git", "-C", str(path), "symbolic-ref", "-q", "HEAD"],
                      stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError(f"Expected detached HEAD: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    repos = json.loads((ROOT / "sources.lock.json").read_text())["repositories"]
    source_root = ROOT / "sources"
    if source_root.is_symlink():
        raise RuntimeError("Refusing symlink sources directory")
    if not args.verify_only:
        source_root.mkdir(exist_ok=True)
    for repo in repos:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", repo["name"]):
            raise ValueError("Invalid repository name")
        if not re.fullmatch(r"[0-9a-f]{40}", repo["commit"]):
            raise ValueError("Expected immutable 40-character commit")
        path = source_root / repo["name"]
        if path.exists() or path.is_symlink():
            verify(path, repo)
        elif args.verify_only:
            raise RuntimeError(f"Missing checkout: {path}")
        else:
            # Interrupted downloads remain inspectable; retry never overwrites them.
            staging = Path(tempfile.mkdtemp(prefix=f".fetch-{repo['name']}-", dir=source_root))
            git(staging, "init", "-q")
            git(staging, "remote", "add", "origin", repo["url"])
            git(staging, "-c", "core.hooksPath=/dev/null", "fetch", "--depth=1", "origin", repo["commit"])
            git(staging, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", repo["commit"])
            verify(staging, repo)
            if path.exists() or path.is_symlink():
                raise RuntimeError(f"Destination appeared during fetch; refusing overwrite: {path}")
            staging.rename(path)
        print(f"OK {repo['name']} {repo['commit']}", flush=True)
    print("All pinned source checkouts verified; no dependency installation performed.")


if __name__ == "__main__":
    main()
