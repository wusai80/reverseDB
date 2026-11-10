#!/usr/bin/env python3
"""Fetch full pinned upstream trees; never execute code or overwrite a checkout."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--all', action='store_true')
    group.add_argument('--project')
    args = parser.parse_args()
    projects = json.loads((ROOT / 'handoff/artifacts.json').read_text())['projects']
    if args.list:
        for spec in projects:
            print(spec['id'], spec['commit'], spec['repository'], '->', spec['destination'])
        return
    selected = projects if args.all else [p for p in projects if p['id'] == args.project]
    if not selected:
        parser.error('Unknown project; use --list')
    for spec in selected:
        dest = (ROOT / spec['destination']).resolve()
        dest.relative_to(ROOT / 'work')
        if dest.exists():
            raise SystemExit(f'Refusing existing path: {dest}. Verify it separately or use a clean clone.')
        if not re.fullmatch(r'[a-f0-9]{40}', spec['commit']):
            raise SystemExit('A full immutable Git commit is required')
        if not spec['repository'].startswith('https://github.com/'):
            raise SystemExit('Unexpected upstream host')
        dest.mkdir(parents=True)
        def git(*argv):
            return subprocess.check_output(
                ['git', '-c', 'core.hooksPath=/dev/null', '-C', str(dest), *argv], text=True)
        git('init', '--quiet')
        git('remote', 'add', 'origin', spec['repository'])
        git('fetch', '--depth=1', 'origin', spec['commit'])
        git('checkout', '--detach', spec['commit'])
        if git('rev-parse', 'HEAD').strip() != spec['commit']:
            raise RuntimeError('Downloaded commit mismatch')
        if git('status', '--porcelain').strip():
            raise RuntimeError('Downloaded working tree is not clean')
        print('Verified', spec['id'], spec['commit'], flush=True)
    print('Sources only. Install/build dependencies and resolve evidence prerequisites before running experiments.')


if __name__ == '__main__':
    main()
