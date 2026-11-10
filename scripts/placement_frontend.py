"""Shared completeness gate for ordered-placement inputs, not a new optimizer."""
from fusion_placement import choose_placement


def select_complete_contract(contract):
    """Unknown costs must never become zero or a fabricated bounded parameter.

    Complete inputs respecting explicit retention go to the original selector unchanged. Partial inputs
    get no plan or objective, including no misleading whole-program no-op plan.
    """
    conflicts = [dict(node=n['id'], reason=n['host_retention']['reason'])
                 for n in contract['nodes']
                 if n.get('host_retention', {}).get('required') is True and n.get('database') is not None]
    if conflicts:
        return dict(status='retention_conflict_no_plan', selection=None, retention_conflicts=conflicts)
    unresolved = [dict(node=n['id'], reason=n.get('cost_unknown_reason', 'missing JDBC execute cost'))
                  for n in contract['nodes'] if n.get('java_calls') is None]
    # A symbolic formula is useful evidence even before a finite domain is
    # justified. Never pass absent/malformed domains to the bounded optimizer.
    parameters = contract.get('parameters', {})
    referenced = {p for n in contract['nodes'] if n.get('java_calls') is not None
                  for p in n['java_calls'].get('terms', {})}
    unbounded = []
    for name in sorted(set(parameters) | referenced):
        bounds = parameters.get(name)
        if not (isinstance(bounds, (list, tuple)) and len(bounds) == 2
                and all(type(v) is int for v in bounds) and 0 <= bounds[0] <= bounds[1]):
            unbounded.append(dict(parameter=name, reason='finite nonnegative integer domain not established'))
    if unbounded:
        return dict(status='incomplete_frontend_no_global_plan', selection=None,
                    unresolved_costs=unresolved, unresolved_parameters=unbounded)
    if unresolved:
        return dict(status='incomplete_frontend_no_global_plan', selection=None, unresolved_costs=unresolved)
    return dict(status='selected_with_original_dp', selection=choose_placement(contract), unresolved_costs=[])
