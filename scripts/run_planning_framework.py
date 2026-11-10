"""Public API example and CLI; recommendations only, no benchmark or deployment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prototype.adaptive_planning.adapters import ArchivedCandidates, reviewed_framework


def run(catalog):
    original = next(c for c in catalog['candidates'] if c['id'] == 'original')
    alternatives = tuple(c for c in catalog['candidates'] if c['id'] != 'original')
    return reviewed_framework(ROOT).plan(context=catalog['context'], original=original,
        providers=(ArchivedCandidates(alternatives),), engine_bindings=catalog['engine_bindings'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = json.dumps(run(json.loads(args.catalog.read_text())), indent=2) + '\n'
    if args.out:
        args.out.write_text(result)
    else:
        print(result, end='')
