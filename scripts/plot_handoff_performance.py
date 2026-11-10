#!/usr/bin/env python3
"""Plot audited paired assessment costs; never infer semantic admission from speed."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

KEY = ('project', 'workload', 'candidate', 'status', 'deployment', 'batch')
REQUIRED = {*KEY, 'split', 'block', 'original_ms', 'candidate_ms'}
STYLES = {'admitted': ('#0072B2', 'o'), 'experimental': ('#D55E00', '^'), 'aa': ('#666666', 's')}


def read_pairs(path):
    groups = defaultdict(list)
    seen = set()
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError('Missing columns: ' + ', '.join(sorted(REQUIRED - set(reader.fieldnames or []))))
        for row in reader:
            if any(not row[k] for k in REQUIRED):
                raise ValueError('Empty required field')
            if row['status'] not in STYLES or row['split'] not in {'selection', 'assessment'}:
                raise ValueError('Unknown admission status or cost split')
            costs = [float(row[k]) for k in ('original_ms', 'candidate_ms')]
            if any(not math.isfinite(v) or v <= 0 for v in costs):
                raise ValueError('Costs must be positive finite milliseconds')
            key = tuple(row[k] for k in KEY)
            identity = (*key, row['split'], row['block'])
            if identity in seen:
                raise ValueError('Duplicate paired block: ' + str(identity))
            seen.add(identity)
            if row['split'] == 'assessment':
                groups[key].append((row['block'], math.log(costs[0]) - math.log(costs[1])))
    if not groups:
        raise ValueError('No independent assessment rows supplied')
    for key, records in groups.items():
        if len(records) < 6:
            raise ValueError(f'At least six paired assessment blocks required: {key}')
    return groups


def summarize(groups):
    rows = []
    for key, records in sorted(groups.items()):
        logs = [value for _, value in sorted(records)]
        rng = random.Random(1729)
        draws = sorted(statistics.mean(rng.choices(logs, k=len(logs))) for _ in range(4000))
        rows.append(dict(zip(KEY, key), blocks=len(logs),
                         geometric_speedup=math.exp(statistics.mean(logs)),
                         lower95=math.exp(draws[99]), upper95=math.exp(draws[3899]),
                         ratios=[math.exp(v) for v in logs]))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = summarize(read_pairs(args.input))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    args.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, max(3.5, 0.65 * len(rows) + 2)))
    labels = []
    for y, row in enumerate(rows):
        color, marker = STYLES[row['status']]
        ratios = row['ratios']
        offset = [(i - (len(ratios) - 1) / 2) * 0.24 / max(1, len(ratios) - 1) for i in range(len(ratios))]
        ax.scatter(ratios, [y + v for v in offset], color=color, marker=marker, alpha=0.25, s=15)
        ax.hlines(y, row['lower95'], row['upper95'], color=color, linewidth=2)
        ax.scatter([row['geometric_speedup']], [y], color=color, marker=marker, s=48, zorder=3)
        labels.append(f"{row['project']} / {row['workload']} / {row['candidate']}\n"
                      f"{row['deployment']} · {row['batch']} · n={row['blocks']}")
    ax.set_yticks(range(len(rows)), labels)
    ax.invert_yaxis()
    ax.axvline(1, color='black', linestyle='--', linewidth=1)
    ax.set_xscale('log')
    ax.set_xlabel('Original / candidate time (geometric paired ratio; >1 is faster)')
    ax.set_title('Independent assessment: raw pairs and descriptive 95% intervals')
    ax.grid(axis='x', alpha=0.2)
    handles = [Line2D([], [], color=c, marker=m, linestyle='', label={
        'admitted': 'Admitted', 'experimental': 'Experimental (not admitted)', 'aa': 'Identical-code A/A'
    }[s]) for s, (c, m) in STYLES.items()]
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, -0.17), ncol=3, frameon=False)
    fig.tight_layout()
    for suffix in ('pdf', 'svg', 'png'):
        fig.savefig(args.out / ('paired-performance.' + suffix), bbox_inches='tight', dpi=180)
    plt.close(fig)
    metadata = dict(schema='reversedb-paired-figure/v1',
        input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        matplotlib_version=matplotlib.__version__, seed=1729, resamples=4000,
        estimator='geometric mean of original/candidate ratios within one assessment batch',
        interval='paired bootstrap percentile, nearest ranks 100 and 3900 of 4000',
        scope='Descriptive intervals; no multiplicity correction, admission, or policy decision',
        rows=rows)
    (args.out / 'figure-manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Wrote {len(rows)} separate contexts to {args.out}')


if __name__ == '__main__':
    main()
