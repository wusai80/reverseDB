#!/usr/bin/env python3
"""Export an explicit source allowlist; omit datasets, raw evidence, and binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
TREE_ROOTS = ('app', 'prototype', 'scripts', 'tests', 'docs')
SUFFIXES = {'.py', '.java', '.c', '.h', '.sh', '.mjs', '.md', '.json', '.sql', '.yaml', '.yml', '.toml'}
SPECIAL = {'Dockerfile', 'BENCHBASE-LICENSE'}
DATA_NAMES = {'candidate_risk_samples.json', 'external_real_review.json',
              'guarded_value_e2e_cohort.json', 'order_e2e_cohort.json',
              'payment_order_e2e_cohort.json', 'value_effect_risk_cohort.json'}
PAPER = ('main.tex', 'references.bib', 'results-tables.tex', 'README.txt', 'build.sh',
         'adaptive-planning-api.txt', 'adaptive-planning-framework.txt',
         'results-summary.txt', 'region-semantics.txt', 'lazy-state-semantics.txt')
SECRET = re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    dest = args.out.resolve()
    if dest.exists():
        parser.error('Export destination must not already exist')
    # An export is a new checkout, not a mutation of the original research tree.
    files = []
    for name in TREE_ROOTS:
        for path in (ROOT / name).rglob('*'):
            if '__pycache__' in path.parts or path.is_symlink() or not path.is_file() or path.name in DATA_NAMES:
                continue
            if path.suffix in SUFFIXES or path.name in SPECIAL:
                files.append((path, path.relative_to(ROOT)))
    for name in ('guide.md', '.gitignore', '.dockerignore', 'regions.json', 'sources.lock.json'):
        files.append((ROOT / name, Path(name)))
    for name in ('README.md', 'artifacts.json', 'external-data.example.json'):
        files.append((ROOT / 'handoff' / name, Path('handoff') / name))
    files.append((ROOT / 'handoff/README.md', Path('README.md')))
    files += [(ROOT / 'paper' / name, Path('paper') / name) for name in PAPER]
    # Validate everything before creating output. JSON in allowlisted source trees
    # is configuration/scenarios/contracts/model parameters, not raw run results.
    for path, relative in files:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError(f'Unexpected source asset: {relative}')
        text = path.read_text()
        if '\0' in text or SECRET.search(text):
            raise ValueError(f'Binary or credential-like content: {relative}')
    dest.mkdir(parents=True)
    inventory = {}
    for path, relative in sorted(files, key=lambda pair: str(pair[1])):
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        inventory[str(relative)] = {'bytes': target.stat().st_size,
                                   'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (dest / 'SOURCE-MANIFEST.json').write_text(json.dumps({
        'schema': 'reversedb-source-export/v1', 'files': inventory,
        'excluded': ['data', 'evidence', 'work', 'sources', 'output', 'replica',
                     'paper/build', 'paper/history', 'paper/results.json', 'paper/writing-notes.txt'],
        'excluded_label_datasets': sorted(DATA_NAMES),
        'note': 'Small scenarios, contracts, model parameters and aggregate manuscript tables are retained. No datasets or raw timings.'
    }, indent=2) + '\n')
    print(json.dumps({'files': len(inventory), 'bytes': sum(v['bytes'] for v in inventory.values()), 'export': str(dest)}))


if __name__ == '__main__':
    main()
