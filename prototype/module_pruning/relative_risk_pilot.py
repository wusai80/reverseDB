"""Relative representation experiment on frozen AST facts, not a safety rule."""
import copy
import hashlib
import json
import math
from pathlib import Path

from candidate_risk_pilot import fit, predict

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path(__file__).with_name('relative_risk_protocol.json')
CLOCK = dict(op='json_long', receiver=dict(op='parameter', position=1, type='org.json.JSONObject'),
             key=dict(op='literal', value='clock_ms'))


def effective(tree):
    if isinstance(tree, dict):
        if tree == dict(op='wall_clock'):
            return copy.deepcopy(CLOCK)
        if tree.get('op') == 'unknown':
            raise ValueError('Unknown selected expression cannot supply a depth')
        return {k:effective(v) for k,v in tree.items()}
    if isinstance(tree, list):
        return [effective(v) for v in tree]
    return tree


def depth(tree):
    if isinstance(tree, dict):
        return int('op' in tree) + max((depth(v) for v in tree.values()), default=0)
    if isinstance(tree, list):
        return max(map(depth, tree), default=0)
    return 0


def selected(facts, method, slot):
    matches = [b for b in facts['bindings'] if method in b['owner'] and len(b['arguments'])==2
               and b['arguments'][0]==dict(op='literal',value=slot)]
    if len(matches)!=1:
        raise ValueError('Nonunique or missing explicit correspondence')
    return matches[0]


def run():
    protocol=json.loads(PROTOCOL.read_text()); hashes={}
    def verified(archive, name):
        folder=ROOT/archive; p=folder/name; data=p.read_bytes()
        h=hashlib.sha256(data).hexdigest()
        assert json.loads((folder/'manifest.json').read_text())[name]==h
        hashes[str(p.relative_to(ROOT))]=h
        return json.loads(data)
    archive=protocol['facts_archive']
    facts={name:verified(archive,name+'.json') for name in ('original','historical','repaired')}
    # Only raw bindings, never binding-comparisons.json or its verdicts.
    snapshot=[]
    for region,mapping in protocol['correspondence'].items():
        reference=selected(facts['original'],mapping['reference_method'],mapping['reference_slot'])
        for version,suffix in [('historical','old'),('repaired','fixed')]:
            candidate=selected(facts[version],mapping['candidate_method'],mapping['candidate_slot'])
            a=effective(reference['arguments'][1]); b=effective(candidate['arguments'][1])
            # Bind raw facts to frozen source files; no current worktree source assumed.
            for binding in (reference,candidate):
                relative=Path(binding['file']).relative_to(ROOT)
                frozen=ROOT/archive/'inputs'/relative
                expected=json.loads((ROOT/archive/'manifest.json').read_text())['inputs/'+str(relative)]
                assert hashlib.sha256(frozen.read_bytes()).hexdigest()==expected
                hashes[str(frozen.relative_to(ROOT))]=expected
            identity=('new' if region=='NewOrder' else 'payment')+'-'+suffix
            snapshot.append(dict(id=identity,region=region,delta=depth(b)-depth(a),
                                 reference_depth=depth(a),candidate_depth=depth(b),
                                 setter_changed=reference['target']!=candidate['target'],
                                 mapped_bindings=1))
            # Numeric feature does not depend on spelling of expression operators.
            def renamed(t):
                if isinstance(t,dict):return {k:('renamed' if k=='op' else renamed(v)) for k,v in t.items()}
                if isinstance(t,list):return list(map(renamed,t))
                return t
            assert depth(renamed(b))-depth(renamed(a))==depth(b)-depth(a)
    snapshot_hash=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
    baseline=verified(protocol['baseline_archive'],'report.json')
    settings=verified(protocol['baseline_archive'],'inputs/prototype/module_pruning/candidate_risk_protocol.json')
    rows=[]
    for row in baseline['rows']:
        relative=next(s for s in snapshot if s['id']==row['id'])
        rows.append(dict(row,x=[row['x'][0],relative['delta']],relative_evidence=relative))
    folds=[]; predictions=[]
    for heldout in ('NewOrder','Payment'):
        train=[r for r in rows if r['region']!=heldout]; test=[r for r in rows if r['region']==heldout]
        model=fit(train,settings)
        changed=[dict(r,y=1-r['y']) if r['region']==heldout else r for r in rows]
        assert fit([r for r in changed if r['region']!=heldout],settings)==model
        reversed_model=fit([dict(r,y=1-r['y']) for r in train],settings)
        assert abs(reversed_model['coefficients'][1]+model['coefficients'][1])<1e-12
        assert model['coefficients'][0]==0
        items=[dict(id=r['id'],y=r['y'],score=predict(model,r['x']),prior=model['prior']) for r in test]
        folds.append(dict(heldout=heldout,model=model,predictions=items));predictions.extend(items)
    metrics=dict(brier=sum((r['score']-r['y'])**2 for r in predictions)/4,
                 log_loss=-sum(r['y']*math.log(r['score'])+(1-r['y'])*math.log1p(-r['score']) for r in predictions)/4,
                 threshold_errors_at_0_5=sum((r['score']>=.5)!=bool(r['y']) for r in predictions))
    # A complete helper still has one required JSON toString after the time fix.
    repaired=next(r for r in baseline['rows'] if r['id']=='new-fixed')
    assert repaired['x'][1]==1 and next(r for r in rows if r['id']=='new-fixed')['x'][1]==0
    new='evidence/neworder-fusion/20261105-4c76a7feb013'
    witness=verified(new,'probe-results.json')['early_write_counterexample']
    assert witness['original']!=witness['mutant']
    helper_fact=facts['historical']['statements']
    targets=[s['sql_alternatives'] for s in helper_fact if 'FusedNewOrder.java' in s['file']]
    assert any(any('neworder_full(' in t for t in ts) and any('neworder_early_write(' in t for t in ts) for ts in targets)
    return dict(scope='Relative-feature retrospective development, not calibration',
                source_hashes=hashes,relative_snapshot_sha256=snapshot_hash,rows=rows,folds=folds,
                metrics=dict(relative=metrics,absolute_baseline=baseline['metrics']['logistic'],prior=baseline['metrics']['training_prior']),
                limitations=dict(source_groups=1,repair_mechanisms=1,bindings_per_helper=1,
                    primary_project_holdout='unavailable',
                    sql_order_control=dict(witness=witness,same_helper_binding_for_full_and_early_write=True,
                        feature_consequence='identical boundary features and score for both SQL modes',
                        scoring='out of timestamp protocol; excluded from training and metrics')),
                checks=['archive and bound source hashes','operator renaming invariance','held-out labels excluded',
                        'training-label reversal changes learned direction','constant binding count has zero weight',
                        'retained JSON serialization does not alter selected time delta','SQL ordering witness retained'],
                new_business_executions=0,deletion_authorized=False,deployed_model=False)


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
