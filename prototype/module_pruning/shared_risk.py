"""A bounded additive risk experiment, never a module-deletion decision.

Pure-Python ridge logistic fit with fixed training-prior intercept. Parameters
are shared across feature combinations; unknown effects still cause abstention.
"""
import math
from collections import Counter

from calibrate_risk import fit as joint_fit
from control_scoring import admitted


def sigmoid(z):
    if z >= 0:
        return 1.0/(1.0+math.exp(-z))
    value = math.exp(z)
    return value/(1.0+value)


def loss_derivatives(beta, design, labels, weights, intercept, penalty):
    size = len(beta)
    loss = penalty*sum(v*v for v in beta)/2
    gradient = [penalty*v for v in beta]
    hessian = [[penalty if i == j else 0.0 for j in range(size)] for i in range(size)]
    for x, y, weight in zip(design, labels, weights):
        z = intercept + sum(b*v for b,v in zip(beta,x))
        q = sigmoid(z)
        loss += weight*(max(z,0)+math.log1p(math.exp(-abs(z)))-y*z)
        for j in range(size):
            gradient[j] += weight*(q-y)*x[j]
            for k in range(size):
                hessian[j][k] += weight*q*(1-q)*x[j]*x[k]
    return loss, gradient, hessian


def solve(matrix, vector):
    """Small deterministic pivoted linear solve; ridge makes Hessian positive."""
    size = len(vector)
    work = [list(row)+[rhs] for row,rhs in zip(matrix,vector)]
    for column in range(size):
        pivot = max(range(column,size), key=lambda i:abs(work[i][column]))
        if abs(work[pivot][column]) < 1e-14:
            raise ValueError("Singular Newton system")
        work[column],work[pivot] = work[pivot],work[column]
        scale = work[column][column]
        work[column] = [v/scale for v in work[column]]
        for row in range(size):
            if row == column:
                continue
            scale = work[row][column]
            work[row] = [a-scale*b for a,b in zip(work[row],work[column])]
    return [row[-1] for row in work]


def fit_shared(rows, base_protocol, names, settings):
    penalty = settings["lambda"]
    if type(penalty) not in (int,float) or not math.isfinite(penalty) or penalty <= 0:
        raise ValueError("Positive finite ridge penalty required")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate feature names")
    required = base_protocol["old_features"]+base_protocol["control_features"]
    if not set(names).issubset(required) or any(not admitted(r.get("features"),required) for r in rows):
        raise ValueError("Complete common-cohort features required")
    # Reuse existing validation and identical smoothed prior. Joint-cell values
    # are not used to learn additive coefficients or make additive predictions.
    reference = joint_fit(rows,{**base_protocol,"features":names})
    counts = Counter(row["family"] for row in rows)
    weights = [1/counts[row["family"]] for row in rows]
    total = sum(weights)
    means = {name:sum(w*row["features"][name] for w,row in zip(weights,rows))/total for name in names}
    values = {name:sorted({row["features"][name] for row in rows}) for name in names}
    active = [name for name in names if len(values[name]) > 1]
    design = [[float(row["features"][name])-means[name] for name in active] for row in rows]
    labels = [row["label"] for row in rows]
    prior = reference["prior"]
    intercept = math.log(prior/(1-prior))
    beta = [0.0]*len(active)
    config = settings["optimizer"]
    history = []
    for iteration in range(config["max_iterations"]+1):
        loss,gradient,hessian = loss_derivatives(beta,design,labels,weights,intercept,penalty)
        norm = max((abs(v) for v in gradient),default=0.0)
        history.append(dict(iteration=iteration,objective=loss,max_abs_gradient=norm))
        if norm <= config["gradient_tolerance"]:
            break
        if iteration == config["max_iterations"]:
            raise RuntimeError("Additive optimizer did not converge")
        direction = solve(hessian,gradient)
        descent = sum(g*d for g,d in zip(gradient,direction))
        if descent <= 0 or not math.isfinite(descent):
            raise RuntimeError("Non-descent Newton direction")
        step = 1.0
        for _ in range(config["max_backtracks"]):
            candidate = [b-step*d for b,d in zip(beta,direction)]
            candidate_loss = loss_derivatives(candidate,design,labels,weights,intercept,penalty)[0]
            if candidate_loss <= loss-config["armijo"]*step*descent:
                beta = candidate
                break
            step *= .5
        else:
            raise RuntimeError("Additive line search failed")
    coefficients = dict.fromkeys(names,0.0)
    coefficients.update(zip(active,beta))
    return dict(model_type="shared-ridge-logistic/v1",domain=base_protocol["domain"],features=names,
                required_features=required,means=means,training_values=values,coefficients=coefficients,
                training_prior=prior,intercept=intercept,training_ids=reference["training_ids"],
                training_groups=reference["training_families"],constant_features=[n for n in names if n not in active],
                lambda_value=penalty,optimizer_history=history,converged=True,
                status="development_only",production_authorized=False)


def predict_shared(model, features, domain):
    if domain != model["domain"]:
        return dict(status="abstain",reason="outside_calibration_domain",probability=None,deletion_authorized=False)
    if not admitted(features,model["required_features"]):
        return dict(status="abstain",reason="unknown_features",probability=None,deletion_authorized=False)
    contributions = {name:model["coefficients"][name]*(float(features[name])-model["means"][name]) for name in model["features"]}
    q = sigmoid(model["intercept"]+sum(contributions.values()))
    return dict(status="development_prediction",probability=q,source="shared_additive_weights",
                contributions=contributions,training_prior=model["training_prior"],
                training_unseen_values=[name for name in model["features"] if features[name] not in model["training_values"][name]],
                calibrated_for_production=False,deletion_authorized=False)
