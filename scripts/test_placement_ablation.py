#!/usr/bin/env python3
"""Fixed region-granularity and selector ablations, modeled costs only."""
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch
from uuid import uuid4

import fusion_placement as placement

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
POLICIES = ['original', 'width_1', 'width_2', 'width_4', 'greedy_longest', 'full_dp']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def choose(contract, policy):
    nodes = contract['nodes']
    host = lambda i: dict(placement='java', start=i, stop=i+1, nodes=[nodes[i]['id']])
    if policy == 'original':
        return [host(i) for i in range(len(nodes))]
    if policy == 'full_dp':
        return placement.choose_placement(contract)['segments']
    if policy.startswith('width_'):
        limit = int(policy.split('_')[1])
        inspector = placement.inspect_interval
        def limited(c, start, stop):
            result = inspector(c, start, stop)
            if stop-start > limit:
                result['allowed'] = False
                result['reasons'].append(dict(kind='experimental_region_width', limit=limit))
            return result
        with patch.object(placement, 'inspect_interval', limited):
            return placement.choose_placement(contract)['segments']
    assert policy == 'greedy_longest'
    # Simple common alternative: at the next unit take the longest admitted
    # interval that strictly reduces worst-case calls; otherwise retain Java.
    upper = {k:v[1] for k,v in contract['parameters'].items()}
    plan, start = [], 0
    while start < len(nodes):
        selected = None
        for stop in range(len(nodes), start, -1):
            interval = placement.inspect_interval(contract, start, stop)
            calls = sum(placement.cost_value(n['java_calls'], upper) for n in nodes[start:stop])
            if interval['allowed'] and calls > 1:
                selected = dict(interval, placement='database')
                break
        if selected is None:
            selected = host(start)
        plan.append(selected)
        start = selected['stop']
    return plan


def main():
    out = ROOT/'evidence/placement-ablation'/('20261109-'+uuid4().hex[:12])
    out.mkdir(parents=True)
    def save(name, value):
        (out/name).write_text(json.dumps(value, indent=2)+'\n')
    save('protocol.json', dict(subjects=['neworder','payment'], policies=POLICIES,
        width='Number of consecutive semantic units, not source classes or SQL queries',
        shared='Identical admission, transaction, codec and effect constraints for every policy',
        measurements='Symbolic JDBC calls over complete declared parameter boxes; no runtime or generated-code claim',
        stop='One fixed evaluation, no tuned width or objective weights'))
    shutil.copy2(__file__, out/'test.py')
    hashes = {}
    for name in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
        source = ROOT/'scripts'/name
        assert sha(source) == sha(FROZEN/'inputs/scripts'/name)
        shutil.copy2(source, out/name)
        hashes[str(source.relative_to(ROOT))] = sha(source)
    rows = []
    for subject in ['neworder','payment']:
        path = FROZEN/(subject+'-contract.json')
        contract = json.loads(path.read_text())
        shutil.copy2(path, out/path.name)
        hashes[str(path.relative_to(ROOT))] = sha(path)
        for policy in POLICIES:
            plan = choose(contract, policy)
            # Independently recheck the plan with the unmodified admission rule.
            validation = placement.validate_plan(contract, plan)
            assert validation['accepted']
            cost = placement.plan_cost(contract, plan)
            lo = {k:v[0] for k,v in contract['parameters'].items()}
            hi = {k:v[1] for k,v in contract['parameters'].items()}
            regions = [s for s in plan if s['placement']=='database']
            row = dict(subject=subject, policy=policy, calls=cost,
                calls_at_minimum=placement.cost_value(cost, lo),
                calls_at_maximum=placement.cost_value(cost, hi),
                moved_units=sum(s['stop']-s['start'] for s in regions),
                regions=len(regions), validated=True,
                runtime_measured=False, generated=False)
            rows.append(row)
            save(subject+'-'+policy+'.json',dict(plan=plan,validation=validation,summary=row))
        full = next(r for r in rows if r['subject']==subject and r['policy']=='full_dp')
        frozen = json.loads((FROZEN/(subject+'-selection.json')).read_text())
        assert full['calls'] == frozen['selected_calls']
        assert [full['calls_at_maximum'],full['moved_units'],full['regions']] == frozen['objective']
        assert all(full['calls_at_maximum']<=r['calls_at_maximum'] for r in rows if r['subject']==subject)
    save('input-hashes.json', hashes)
    save('summary.json',dict(status='passed',rows=rows, runtime_executions=0,
        limits='Two existing contracts; width is a sensitivity restriction on available regions. This does not establish learned transfer, general optimality, or speedups. Ties with greedy must be reported.'))
    save('manifest.json',{str(p.relative_to(out)):sha(p) for p in out.iterdir() if p.is_file()})
    print(out)


if __name__ == '__main__':
    main()
