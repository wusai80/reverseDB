"""One retrospective four-candidate diagnostic; no mutation or DB execution."""
import hashlib
import json
import math
import re
from pathlib import Path

from shared_risk import loss_derivatives, sigmoid, solve

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path(__file__).with_name('candidate_risk_protocol.json')


def features(source):
    # Sufficient only for these frozen generated sources (no Java text blocks).
    if '"""' in source:
        raise ValueError('Java text blocks outside pilot scope')
    masked = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ' ' * len(m.group()), source)
    receivers = set(re.findall(r'\bPreparedStatement\s+(\w+)\s*=', masked))
    arguments = []
    for receiver in sorted(receivers):
        for call in re.finditer(r'\b' + re.escape(receiver) + r'\.set[A-Z]\w*\s*\(', masked):
            start = call.end(); end = start; depth = 1
            while end < len(masked) and depth:
                depth += (masked[end] == '(') - (masked[end] == ')')
                end += 1
            if depth:
                raise ValueError('Unbalanced binding call')
            arguments.append(masked[start:end-1])
    if not arguments:
        raise ValueError('No bindings in frozen helper')
    return [len(arguments), sum(len(re.findall(r'\.toString\s*\(', a)) for a in arguments)]


def fit(rows, protocol):
    n = len(rows); weights = [1/n] * n
    prior = (1 + sum(w*r['y'] for w,r in zip(weights, rows))) / 3
    means = [sum(r['x'][j] for r in rows)/n for j in range(2)]
    design = [[v-m for v,m in zip(r['x'], means)] for r in rows]
    intercept = math.log(prior/(1-prior)); beta = [0., 0.]
    settings = protocol['optimizer']; penalty = protocol['lambda']
    labels = [r['y'] for r in rows]
    for iteration in range(settings['max_iterations']+1):
        loss, gradient, hessian = loss_derivatives(beta, design, labels, weights, intercept, penalty)
        if max(map(abs, gradient)) <= settings['gradient_tolerance']:
            break
        if iteration == settings['max_iterations']:
            raise RuntimeError('No convergence')
        direction = solve(hessian, gradient)
        descent = sum(g*d for g,d in zip(gradient, direction)); step = 1.
        for _ in range(settings['max_backtracks']):
            candidate = [b-step*d for b,d in zip(beta, direction)]
            new_loss = loss_derivatives(candidate, design, labels, weights, intercept, penalty)[0]
            if new_loss <= loss-settings['armijo']*step*descent:
                beta = candidate; break
            step *= .5
        else:
            raise RuntimeError('Line search failed')
    return dict(coefficients=beta, means=means, intercept=intercept, prior=prior,
                iterations=iteration, training_ids=[r['id'] for r in rows],
                training_values=[sorted({r['x'][j] for r in rows}) for j in range(2)])


def predict(model, x):
    return sigmoid(model['intercept'] + sum(b*(v-m) for b,v,m in zip(model['coefficients'], x, model['means'])))


def run():
    protocol = json.loads(PROTOCOL.read_text()); archive = ROOT/protocol['source_archive']
    manifest = json.loads((archive/'manifest.json').read_text()); sources = {}
    def verified(name):
        data = (archive/name).read_bytes(); digest = hashlib.sha256(data).hexdigest()
        assert manifest[name] == digest, name
        sources[name] = digest
        return data
    rows = []
    # Features are obtained before opening outcomes; no labels/version names enter features().
    for identity in protocol['candidates']:
        group = 'NewOrder' if identity.startswith('new-') else 'Payment'
        helper = 'FusedNewOrder.java' if group == 'NewOrder' else 'SegmentedPayment.java'
        source = verified('generated/'+identity+'/'+helper).decode()
        x = features(source)
        # Comments/string contents and helper renaming cannot manufacture counts.
        assert features(source + '\n// fake.setString(1,x.toString());\n') == x
        assert features(source.replace(helper[:-5], 'RenamedBridge')) == x
        assert features(source.replace('"clock_ms"', '"fake.toString()"')) == x
        rows.append(dict(id=identity, source_group='tpcc-app', region=group, x=x))
    feature_digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    outcomes = json.loads(verified('probe-results.json'))
    assert outcomes['status'] == 'passed' and outcomes['all_rollbacks_restore_nine_tables']
    clocks = json.loads(verified('protocol.json'))['clocks']
    for row in rows:
        arm = 'historical' if row['id'].endswith('-old') else 'fixed'
        cases = ['NewOrder'] if row['region']=='NewOrder' else ['PaymentGC','PaymentBC']
        differences = 0; comparisons = 0
        for case in cases:
            for clock in clocks:
                select = lambda c: c['case']==case and c['clock_ms']==clock
                original = [c for c in outcomes['checks'] if select(c) and c['arm']=='original']
                candidate = [c for c in outcomes['checks'] if select(c) and c['arm']==arm]
                errors = [c for c in outcomes['negative_controls'] if select(c)] if arm=='historical' else []
                assert len(original)==1 and len(candidate)+len(errors)==1
                differences += bool(errors) or any(original[0][k]!=candidate[0][k] for k in ('result','nine_table_state'))
                comparisons += 1
        row.update(y=int(differences>0), observed_differences=differences, comparisons=comparisons)
    folds = []; predictions = []
    for heldout in ('NewOrder', 'Payment'):
        train = [r for r in rows if r['region']!=heldout]
        test = [r for r in rows if r['region']==heldout]
        model = fit(train, protocol)
        assert model['coefficients'][0] == 0.  # This count is constant within each training region.
        # Held-out outcomes cannot affect fit or predictions.
        flipped = [dict(r, y=1-r['y']) if r['region']==heldout else r for r in rows]
        assert fit([r for r in flipped if r['region']!=heldout], protocol) == model
        fold_predictions = [dict(id=r['id'], y=r['y'], score=predict(model,r['x']), prior=model['prior'],
                            unseen_feature_indices=[j for j,v in enumerate(r['x']) if v not in model['training_values'][j]]) for r in test]
        predictions.extend(fold_predictions)
        positive = next(p for p in fold_predictions if p['y']==1)
        negative = next(p for p in fold_predictions if p['y']==0)
        folds.append(dict(heldout_region=heldout, model=model, predictions=fold_predictions,
                          risk_pair_ranked_correctly=positive['score']>negative['score']))
    def metrics(key):
        return dict(brier=sum((p[key]-p['y'])**2 for p in predictions)/len(predictions),
                    log_loss=-sum(p['y']*math.log(p[key])+(1-p['y'])*math.log1p(-p[key]) for p in predictions)/len(predictions))
    # Constant features with balanced labels must recover the prior exactly.
    constant = [dict(r, x=[0,0]) for r in rows[:2]]
    constant_model = fit(constant, protocol)
    assert predict(constant_model,[0,0]) == constant_model['prior']
    return dict(scope='Retrospective four-candidate development diagnostic; not external validation or calibrated risk',
                source_hashes=sources, feature_order=protocol['features'], feature_snapshot_sha256=feature_digest,
                rows=rows, folds=folds, metrics=dict(logistic=metrics('score'), training_prior=metrics('prior')),
                primary_source_holdout='unavailable: one source group; original inventory split unchanged',
                checks=['archive hashes', 'feature invariance to comments/string contents/helper name',
                        'complete original/candidate outcome pairing', 'held-out labels excluded from fitting',
                        'training-constant feature zero weight', 'constant features recover prior'],
                new_business_executions=0, deployed_model=False, deletion_authorized=False)


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
