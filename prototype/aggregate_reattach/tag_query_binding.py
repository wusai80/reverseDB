"""Two audited relation bindings sharing one binder/lifecycle, not general B3.

Uses live ORM/schema facts, pinned runtime evidence and explicit caller assumptions.
The fixed-data assumption is not inferred from a transaction ID or query hash.
"""
import hashlib
import inspect
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import CodeType

import django
from django.contrib.auth.models import User
from django.core.exceptions import EmptyResultSet
from django.db import connection
from django.db.models import Case, Count, IntegerField, Value, When
from django.db.models.expressions import Col
from django.db.models.manager import Manager
from django.db.models.lookups import IsNull
from django_softdelete.managers import SoftDeleteManager

from documents import models, permissions
from documents.models import CustomField, CustomFieldInstance, Document, Tag
from tag_counts_b2 import render_count_sql
from transform_contract import Evidence, REQUIREMENTS, State, UnsupportedPattern, prepare_plan, recover_region


CONSUMER = "immediate-pk-ascending-int-pairs/v1"
PROFILE = Path(__file__).with_name("tag_binding_profile.json")
CUSTOM_PROFILE = Path(__file__).with_name("custom_field_binding_profile.json")


@dataclass(frozen=True)
class RelationSpec:
    key: str
    target_model: type
    link_model: type
    relation_field: str
    manager_class: type
    manager_null_field: str | None
    automatic_through: bool


# Explicit audited registry, not automatic model/call-site discovery.
SPECS = {
    "tag": RelationSpec("tag", Tag, Document.tags.through, "tag", Manager, None, True),
    "custom_field": RelationSpec("custom_field", CustomField, CustomFieldInstance,
                                 "field", SoftDeleteManager, "deleted_at", False),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def original(target, user):
    return original_for("tag", target, user)


def original_for(spec_key, target, user):
    spec = SPECS[spec_key]
    return list(permissions.annotate_document_count_for_related_queryset(
        target, spec.link_model, spec.link_model._meta.get_field(spec.relation_field).attname, user=user,
    ).values_list("pk", "document_count"))


@dataclass
class Candidate:
    # Process-local, not a persisted execution capability. The executor rebuilds
    # SQL from fresh ORM facts instead of trusting this object's payload.
    fingerprint: str
    used: bool = False


@dataclass
class Binding:
    plan: dict
    facts: dict
    candidate: Candidate | None
    sql: str | None = None
    params: tuple = ()


class UnboundEntry(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise UnboundEntry(reason)


def _compile(queryset):
    sql, params = queryset.query.sql_with_params()
    return {"sql": sql, "params": list(params), "alias": queryset.db}


def _load_profile(path):
    """Invalid audit configuration means no admission, not a business failure.

    Catch only configuration I/O/decoding errors here. Database execution errors
    and errors from the caller's fallback must propagate without retry.
    """
    try:
        profile = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnboundEntry(f"audit profile unavailable: {type(exc).__name__}: {exc}") from exc
    _require(isinstance(profile, dict), "audit profile must be an object")
    _require(isinstance(profile.get("django_version"), str) and bool(profile["django_version"]),
             "audit profile requires django_version")
    _require(type(profile.get("postgres_version")) is int and profile["postgres_version"] > 0,
             "audit profile requires integer postgres_version")
    def valid_hash(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    _require(all(valid_hash(profile.get(key)) for key in ("source_sha256", "models_sha256")),
             "audit profile requires source/model SHA-256 hashes")
    converters = profile.get("converter_files")
    _require(isinstance(converters, dict) and bool(converters)
             and all(isinstance(name, str) and "\x00" not in name and Path(name).is_absolute()
                     and valid_hash(value) for name, value in converters.items()),
             "audit profile requires converter file paths and SHA-256 hashes")
    return profile


def _runtime_profile(source, profile):
    _require(django.get_version() == profile["django_version"], "Django version changed")
    _require(connection.pg_version == profile["postgres_version"], "PostgreSQL version changed")
    _require(hashlib.sha256(source.encode()).hexdigest() == profile["source_sha256"], "source revision changed")
    _require(hashlib.sha256(Path(models.__file__).read_bytes()).hexdigest() == profile["models_sha256"], "model revision changed")
    compiled = compile(source, permissions.__file__, "exec", dont_inherit=True, optimize=sys.flags.optimize)
    definitions = {c.co_name: c for c in compiled.co_consts if isinstance(c, CodeType)}
    for name in ("annotate_document_count_by_ids", "annotate_document_count_for_related_queryset",
                 "permitted_document_ids", "permitted_object_ids"):
        _require(getattr(permissions, name).__code__ == definitions[name], "runtime function changed: " + name)
    for name, symbol in {"Count": Count, "Value": Value, "Case": Case, "When": When,
                         "IntegerField": IntegerField}.items():
        _require(getattr(permissions, name) is symbol, "runtime ORM symbol changed: " + name)
    for filename, expected in profile["converter_files"].items():
        _require(hashlib.sha256(Path(filename).read_bytes()).hexdigest() == expected,
                 "audited converter file changed")


def _schema_and_context(spec):
    """Fresh catalog facts; combine column/RLS reads, retain all constraints."""
    schema = {}
    key_field = spec.link_model._meta.get_field(spec.relation_field)
    relations = ((spec.target_model, ("id",)), (Document, ("id",)),
                 (spec.link_model, (key_field.column, "document_id")))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.name, c.oid, c.relrowsecurity, c.relforcerowsecurity, "
            "a.attname, a.attnotnull, format_type(a.atttypid, a.atttypmod) "
            "FROM unnest(%s::text[]) AS t(name) "
            "JOIN pg_class c ON c.oid = t.name::regclass "
            "LEFT JOIN pg_attribute a ON a.attrelid = c.oid "
            "AND a.attnum > 0 AND NOT a.attisdropped",
            [[model._meta.db_table for model, _ in relations]],
        )
        catalogs = {}
        for table, oid, rls, force_rls, name, nonnull, dtype in cursor.fetchall():
            item = catalogs.setdefault(table, {"oid": oid, "rls": rls,
                                              "force_rls": force_rls, "columns": {}})
            if name is not None:
                item["columns"][name] = [nonnull, dtype]
        for model, keys in relations:
            table = model._meta.db_table
            _require(table in catalogs, "relation catalog entry missing")
            item = catalogs[table]
            columns = item["columns"]
            _require(all(columns.get(k) == [True, "integer"] for k in keys), "key schema changed")
            constraints = connection.introspection.get_constraints(cursor, table)
            if model in (spec.target_model, Document):
                _require(any(c["primary_key"] and c["columns"] == ["id"] for c in constraints.values()),
                         "target/document primary key changed")
            _require(not item["rls"] and not item["force_rls"], "unbound RLS policy")
            schema[table] = {"oid": item["oid"], "keys": {k: columns[k] for k in keys},
                             "constraints": constraints, "rls": item["rls"], "force_rls": item["force_rls"]}
            if model is spec.link_model and spec.manager_null_field:
                _require(spec.manager_null_field in columns and
                         columns[spec.manager_null_field] == [False, "timestamp with time zone"],
                         "soft-delete column schema changed")
                schema[table]["manager_column"] = columns[spec.manager_null_field]
        cursor.execute("SELECT current_database(), current_user, current_setting('search_path'), "
                       "current_setting('transaction_isolation'), pg_backend_pid(), txid_current()")
        context = list(cursor.fetchone())
    return schema, context


def _manager_facts(spec):
    links = spec.link_model
    _require(type(links.objects) is spec.manager_class, "through manager class changed")
    _require((links._meta.auto_created is Document) if spec.automatic_through
             else not links._meta.auto_created, "through model kind changed")
    queryset = links.objects.all()
    where = queryset.query.where
    _require(not where.negated and where.connector == "AND", "unbound manager predicate shape")
    if spec.manager_null_field is None:
        _require(not where.children, "through manager filter changed")
    else:
        _require(len(where.children) == 1 and type(where.children[0]) is IsNull,
                 "unbound soft-delete manager predicates")
        condition = where.children[0]
        _require(isinstance(condition.lhs, Col) and condition.lhs.target.model is links
                 and condition.lhs.target.column == spec.manager_null_field and condition.rhs is True,
                 "soft-delete filter changed")
    return {"class": type(links.objects).__module__ + "." + type(links.objects).__name__,
            "predicate": str(where), "query": _compile(queryset),
            "source": inspect.getsource(type(links.objects).get_queryset)}


def bind_tag_query(user, target, declarations, consumer=CONSUMER):
    return bind_count_query("tag", user, target, declarations, consumer)


def bind_count_query(spec_key, user, target, declarations, consumer=CONSUMER):
    """Bind one registered entry; no business rows or aggregates are fetched."""
    if spec_key not in SPECS:
        raise ValueError("Unknown relation specification")
    spec = SPECS[spec_key]
    declaration_names = {k for k, v in REQUIREMENTS.items() if v == State.DECLARED}
    if set(declarations) - declaration_names:
        raise ValueError("Only caller declarations may be supplied; query facts are computed here")
    try:
        source = Path(permissions.__file__).read_text()
        region = recover_region(source, "annotate_document_count_by_ids")
        profile = _load_profile(PROFILE if spec_key == "tag" else CUSTOM_PROFILE)
        _require(connection.vendor == "postgresql" and connection.in_atomic_block,
                 "requires audited PostgreSQL and an explicit transaction scope")
        _require(type(user) is User and not user._state.adding and user._state.db == "default"
                 and user.is_active and user.is_superuser, "unbound identity")
        canonical = spec.target_model.objects.order_by("pk")
        _require(type(target) is type(canonical) and target.model is spec.target_model and target.db == "default",
                 "unbound target model/query class/database")
        _require(target._result_cache is None and not target.query.select_for_update, "cached or locking queryset")
        # Projection can hide extra selections, annotations and aliases that
        # still affect the original helper's final document_count consumer.
        # Reject these before values("pk") discards their evidence.
        _require(not target.query.extra and not target.query.annotations
                 and not target.query._filtered_relations and target._fields is None,
                 "unbound target extra/annotations/aliases/projection")
        target_fragment = _compile(target.values("pk"))
        _require(target_fragment == _compile(canonical.values("pk")), "unbound target query shape")
        _require(consumer == CONSUMER, "unbound consumer")
        _runtime_profile(source, profile)
        links = spec.link_model
        manager = _manager_facts(spec)
        for key, endpoint in ((spec.relation_field, spec.target_model), ("document", Document)):
            field = links._meta.get_field(key)
            _require(not field.null and field.target_field.get_internal_type() == "AutoField"
                     and field.target_field.model is endpoint,
                     "key model metadata changed")
        allowed = permissions.permitted_document_ids(user)
        _require(allowed.db == target.db, "permission relation database differs")
        # Instantiate only the operators recovered by the accepted source grammar.
        # The call-site/model mapping is explicit in the audited registry.
        value_field = region.roles["count_value_default"]
        _require(value_field == "document_id", "unbound count field")
        relation_field = links._meta.get_field(spec.relation_field)
        key_name = relation_field.attname
        counts = links.objects.filter(**{f"{key_name}__in": target.values("pk"),
                                         f"{value_field}__in": allowed}
                                      ).values(key_name).annotate(c=Count(value_field, distinct=region.roles["distinct"]))
        group = counts.query.group_by
        _require(isinstance(group, tuple) and len(group) == 1 and isinstance(group[0], Col)
                 and group[0].target is relation_field, "group key is not unique by construction")
        aggregate = counts.query.annotations["c"]
        _require(type(aggregate) is Count and aggregate.distinct and counts.db == target.db,
                 "aggregate or database changed")
        aggregate_fragment = _compile(counts)
        schema, context = _schema_and_context(spec)
        facts = {
            "relation_spec": {"key": spec.key, "target_model": spec.target_model._meta.label,
                              "link_model": links._meta.label, "group_key": key_name,
                              "manager_null_field": spec.manager_null_field}, "manager": manager,
            "source_sha256": region.source_sha256, "profile_sha256": digest(profile),
            "target": target_fragment, "counts": aggregate_fragment,
            "allowed_ids": _compile(allowed), "links": _compile(links.objects.all()),
            "schema": schema, "context": context, "consumer": consumer,
            "identity": [user.pk, user.is_active, user.is_superuser, user._state.db],
            "declarations": {k: {"state": v.state.value, "reference": v.reference}
                             for k, v in declarations.items()},
        }
        fingerprint = digest(facts)
        checks = {k: Evidence(State.VERIFIED, f"live-binding:{fingerprint}:{k}")
                  for k, v in REQUIREMENTS.items() if v == State.VERIFIED}
        checks.update(declarations)
        plan = prepare_plan(region, region.source_sha256, checks)
        if plan["decision"] != "eligible_for_lowering":
            return Binding(plan, facts, None)
        sql, params = render_count_sql(target_fragment["sql"], target_fragment["params"],
                                       aggregate_fragment["sql"], aggregate_fragment["params"], key_name)
        plan["binding_fingerprint"] = fingerprint
        plan["lowering"]["execution"] = "guarded_one_use_relation_executor"
        return Binding(plan, facts, Candidate(fingerprint), sql, params)
    except (UnsupportedPattern, UnboundEntry, EmptyResultSet) as exc:
        return Binding({"decision": "rejected", "action": "keep_original", "reason": str(exc)}, {}, None)


def execute_tag_candidate(binding, user, target, declarations, consumer=CONSUMER, *, fallback=None):
    return execute_count_candidate("tag", binding, user, target, declarations, consumer, fallback=fallback)


def execute_count_candidate(spec_key, binding, user, target, declarations, consumer=CONSUMER, *, fallback=None):
    """Rebind immediately before execution. SQL/DB errors propagate, never retry."""
    if spec_key not in SPECS:
        raise ValueError("Unknown relation specification")
    if fallback is None:
        fallback = (lambda: original(target, user)) if spec_key == "tag" else lambda: original_for(spec_key, target, user)
    token = binding.candidate
    if token is None or token.used:
        return fallback(), {"route": "original", "reason": "missing_or_used_candidate"}
    token.used = True
    current = bind_count_query(spec_key, user, target, declarations, consumer)
    if current.candidate is None or current.candidate.fingerprint != token.fingerprint:
        return fallback(), {"route": "original", "reason": "binding_changed_or_unproven",
                                       "current_decision": current.plan}
    # Use freshly reconstructed SQL, not mutable SQL fields in the supplied binding.
    with connection.cursor() as cursor:
        cursor.execute(current.sql, current.params)
        rows = [(int(pk), int(count)) for pk, count in cursor.fetchall()]
    return rows, {"route": "candidate", "binding_fingerprint": token.fingerprint}
