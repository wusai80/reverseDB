"""Offline interface for one aggregate/map/CASE pattern; no execution or SQL lowering.

Structural matching is deliberately conservative. Runtime checks are supplied by
an audited binder; declarations are caller assumptions, never inferred from tests.
An eligible plan is conditional on both and does not authorize deployment.
"""
import ast
import hashlib
from dataclasses import dataclass
from enum import Enum


TEMPLATE = '''
def region(_T, _L, _key, _A, _value="document_id"):
    _counts = (_L.objects.filter(**{
        f"{_key}__in": _T.values("pk"),
        f"{_value}__in": _A,
    }).values(_key).annotate(c=Count(_value, distinct=True)))
    _mapping = {_row[_key]: _row["c"] for _row in _counts}
    if not _mapping:
        return _T.annotate(document_count=Value(0, output_field=IntegerField()))
    return _T.annotate(document_count=Case(
        *(When(pk=_pk, then=Value(_n)) for _pk, _n in _mapping.items()),
        default=Value(0), output_field=IntegerField()))
'''
VARIABLES = {"_T", "_L", "_key", "_A", "_value", "_counts", "_mapping", "_row", "_pk", "_n"}
SYMBOLS = {"Count", "Value", "IntegerField", "Case", "When"}


class State(str, Enum):
    VERIFIED = "verified"
    DECLARED = "declared"
    REFUTED = "refuted"
    UNKNOWN = "unknown"


# A binder must establish the first group; a caller must explicitly supply the
# second group. Neither kind of evidence may silently substitute for the other.
REQUIREMENTS = {
    "entry_scope": State.VERIFIED,
    "relation_predicates": State.VERIFIED,
    "key_equality_and_nullability": State.VERIFIED,
    "group_key_unique": State.VERIFIED,
    "consumer_total_order": State.VERIFIED,
    "integer_result_conversion": State.VERIFIED,
    "same_database_and_context": State.VERIFIED,
    "fixed_read_state": State.DECLARED,
    "immediate_integer_pair_consumer": State.DECLARED,
    "successful_execution_scope": State.DECLARED,
    "no_observable_read_effects": State.DECLARED,
    "pinned_runtime_bindings": State.DECLARED,
}


@dataclass(frozen=True)
class Evidence:
    state: State
    reference: str


@dataclass(frozen=True)
class Region:
    source_sha256: str
    function: str
    line: int
    roles: dict


class UnsupportedPattern(ValueError):
    pass


def recover_region(source, function):
    """Recognize exactly the supported AST family without importing/executing it.

Alpha-renaming of parameters/local variables and comments/docstrings is allowed.
ORM operator names, output aliases and zero/distinct semantics are fixed in v0.
"""
    module = ast.parse(source)
    candidates = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == function]
    if len(candidates) != 1:
        raise UnsupportedPattern("Expected one top-level function")
    node = candidates[0]
    args = node.args
    if (node.decorator_list or len(args.args) != 5 or args.posonlyargs or args.kwonlyargs
            or args.vararg or args.kwarg or len(args.defaults) != 1
            or not isinstance(args.defaults[0], ast.Constant)
            or not isinstance(args.defaults[0].value, str)):
        raise UnsupportedPattern("Unsupported signature or decorator")
    imports = {a.name for n in module.body if isinstance(n, ast.ImportFrom)
               and n.module == "django.db.models" for a in n.names if a.asname is None}
    if not SYMBOLS <= imports:
        raise UnsupportedPattern("Required direct Django imports not found")
    # Presence of imports is not proof against runtime rebinding: the caller's
    # pinned_runtime_bindings declaration remains mandatory.
    bindings = dict(zip(("_T", "_L", "_key", "_A", "_value"), (a.arg for a in args.args)))
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    template_body = ast.parse(TEMPLATE).body[0].body

    def match(expected, actual):
        if type(expected) is not type(actual):
            return False
        if isinstance(expected, ast.Name) and expected.id in VARIABLES:
            prior = bindings.setdefault(expected.id, actual.id)
            return prior == actual.id and type(expected.ctx) is type(actual.ctx)
        if isinstance(expected, ast.AST):
            return all(match(getattr(expected, f), getattr(actual, f)) for f in expected._fields)
        if isinstance(expected, list):
            return len(expected) == len(actual) and all(match(a, b) for a, b in zip(expected, actual))
        return expected == actual

    if not match(template_body, body):
        raise UnsupportedPattern("Body is outside the supported aggregate/map/CASE grammar")
    if len(set(bindings.values())) != len(bindings) or set(bindings.values()) & SYMBOLS:
        raise UnsupportedPattern("Variable aliasing or shadowed ORM symbols")
    return Region(hashlib.sha256(source.encode()).hexdigest(), function, node.lineno, {
        "target_queryset": bindings["_T"], "through_model": bindings["_L"],
        "group_key_parameter": bindings["_key"], "allowed_ids": bindings["_A"],
        "count_value_parameter": bindings["_value"], "count_value_default": args.defaults[0].value,
        "aggregate_variable": bindings["_counts"], "map_variable": bindings["_mapping"],
        "target_key": "pk", "aggregate_alias": "c", "output_alias": "document_count",
        "distinct": True, "missing_value": 0,
    })


def prepare_plan(region, bound_source_sha256, checks):
    """Pure, serializable review decision; supplied VERIFIED evidence is trusted.

This interface cannot verify the truth or freshness of an external audit. A live
binder and lowerer are future work. Unknown conditions always keep the original.
"""
    unknown_keys = set(checks) - set(REQUIREMENTS)
    if unknown_keys:
        raise ValueError("Unknown check names: " + ", ".join(sorted(unknown_keys)))
    rejected = bound_source_sha256 is not None and bound_source_sha256 != region.source_sha256
    obligations = [{"name": "source_binding", "status": (
        "refuted" if rejected else ("unknown" if bound_source_sha256 is None else "satisfied")),
        "bound_source_sha256": bound_source_sha256}]
    for name, required in REQUIREMENTS.items():
        item = checks.get(name, Evidence(State.UNKNOWN, ""))
        accepted = item.state == required and bool(item.reference.strip())
        rejected |= item.state == State.REFUTED
        obligations.append({"name": name, "required": required.value,
                            "supplied": item.state.value, "reference": item.reference,
                            "status": "satisfied" if accepted else (
                                "refuted" if item.state == State.REFUTED else "unknown")})
    ready = not rejected and all(o["status"] == "satisfied" for o in obligations)
    return {
        "schema_version": 1, "pattern": "group_distinct_count/map/zero_case",
        "source_sha256": region.source_sha256, "function": region.function,
        "source_line": region.line, "roles": region.roles,
        "decision": "eligible_for_lowering" if ready else ("rejected" if rejected else "needs_evidence"),
        "action": "prepare_candidate_only" if ready else "keep_original",
        "obligations": obligations,
        "lowering": {"operator": "left_join_unique_counts", "zero_default": 0,
                     "preserve": ["target_bag", "original_group_query_and_parameters", "consumer_order"],
                     "execution": "not_implemented"} if ready else None,
    }
