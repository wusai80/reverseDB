"""Offline declarative call-site candidates, never runtime admission.

Supported subset: directly declared class attributes and the audited count mixin
wiring. Symbols are resolved through explicit imports; expressions aren't run.
"""
import ast
import hashlib
import json


HELPER = "documents.permissions.annotate_document_count_for_related_queryset"
CONFIG = ("model", "document_count_through", "document_count_source_field")
GET_QUERYSET = '''
def get_queryset(self):
    base_qs = super().get_queryset()
    if self.document_count_through:
        user = getattr(getattr(self, "request", None), "user", None)
        return HELPER(base_qs, through_model=self.document_count_through,
                      related_object_field=self._get_document_count_source_field(), user=user)
    filter = self.get_document_count_filter()
    return base_qs.annotate(document_count=Count(self.document_count_related_name,
                                               filter=filter, distinct=True))
'''
GET_FIELD = '''
def _get_document_count_source_field(self):
    if self.document_count_source_field is None:
        msg = "document_count_source_field must be set when document_count_through is configured"
        raise ValueError(msg)
    return self.document_count_source_field
'''


def source_hash(source):
    return hashlib.sha256(source.encode()).hexdigest()


def imports_and_rebindings(module):
    imports, rebound = {}, set()
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                name = alias.asname or alias.name
                if name in imports:
                    rebound.add(name)
                imports[name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name in imports:
                    rebound.add(name)
                imports[name] = alias.name if alias.asname else name
    # Restrict this to module-level statements; do not confuse local assignments
    # inside functions/classes with rebinding a module import.
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in imports:
                rebound.add(node.name)
            continue
        rebound.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                       and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id in imports)
    return imports, rebound


def symbol(node, imports, rebound):
    if isinstance(node, ast.Name):
        return imports.get(node.id) if node.id not in rebound else None
    if isinstance(node, ast.Attribute):
        base = symbol(node.value, imports, rebound)
        return f"{base}.{node.attr}" if base else None
    return None


def attributes(cls):
    found = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and value is not None:
                found.setdefault(target.id, []).append((value, node.lineno))
    return found


def _body(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)


def wiring(cls, imports, rebound):
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    get_query = methods.get("get_queryset")
    getter = methods.get("_get_document_count_source_field")
    if not get_query or not getter:
        return None
    calls = [n for n in ast.walk(get_query) if isinstance(n, ast.Call)
             and symbol(n.func, imports, rebound) == HELPER]
    if len(calls) != 1:
        return None
    # Normalize imported aliases, without executing imports or the class body.
    normalized = ast.parse(ast.unparse(get_query)).body[0]
    class Normalize(ast.NodeTransformer):
        def visit_Name(self, node):
            resolved = symbol(node, imports, rebound)
            if resolved == HELPER:
                node.id = "HELPER"
            elif resolved == "django.db.models.Count":
                node.id = "Count"
            return node
    Normalize().visit(normalized)
    accepted = (not get_query.decorator_list and not getter.decorator_list
                and _body(normalized) == _body(ast.parse(GET_QUERYSET).body[0])
                and _body(getter) == _body(ast.parse(GET_FIELD).body[0]))
    return {"mixin": cls.name, "line": cls.lineno, "method_line": get_query.lineno,
            "helper_call_line": calls[0].lineno, "helper": HELPER,
            "status": "recognized_wiring" if accepted else "unsupported_wiring"}


def extract_callsites(source):
    module = ast.parse(source)
    imports, rebound = imports_and_rebindings(module)
    classes = [n for n in module.body if isinstance(n, ast.ClassDef)]
    adapters = {c.name: w for c in classes if (w := wiring(c, imports, rebound)) is not None}
    candidates, skipped = [], []
    for cls in classes:
        bases = [b.id for b in cls.bases if isinstance(b, ast.Name) and b.id in adapters]
        if not bases:
            continue
        attrs = attributes(cls)
        # This branch of the mixin needs a through model. Classes using the
        # direct-FK aggregate are outside this pattern, not evidence of dead code.
        if not any(k in attrs for k in CONFIG[1:]):
            skipped.append({"class": cls.name, "reason": "no_local_through_configuration", "line": cls.lineno})
            continue
        problems, values, locations = [], {}, {}
        for name in CONFIG:
            declarations = attrs.get(name, [])
            if len(declarations) != 1:
                problems.append(f"missing_or_multiple:{name}")
                values[name] = None
                continue
            node, line = declarations[0]
            locations[name] = {"line": line, "expression": ast.unparse(node)}
            if name == "document_count_source_field":
                value = node.value if isinstance(node, ast.Constant) and type(node.value) is str else None
            else:
                value = symbol(node, imports, rebound)
            values[name] = value
            if value is None:
                problems.append(f"dynamic_or_unresolved:{name}")
        if len(bases) != 1 or adapters[bases[0]]["status"] != "recognized_wiring":
            problems.append("ambiguous_or_unsupported_mixin_wiring")
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in (
                    "get_queryset", "_get_document_count_source_field"):
                problems.append("local_override:" + method.name)
        qs = attrs.get("queryset", [])
        queryset = ast.unparse(qs[0][0]) if len(qs) == 1 else None
        order = None
        if len(qs) == 1 and isinstance(qs[0][0], ast.Call):
            call = qs[0][0]
            if isinstance(call.func, ast.Attribute) and call.func.attr == "order_by":
                order = [ast.unparse(a) for a in call.args]
        candidates.append({"class": cls.name, "line": cls.lineno,
                           "source_sha256": source_hash(source), "configuration": values,
                           "locations": locations, "wiring": [adapters[b] for b in bases],
                           "queryset_expression": queryset, "declared_ordering": order,
                           "status": "candidate" if not problems else "needs_review", "problems": problems,
                           "unresolved": ["runtime_mro_and_class_decorators", "super_get_queryset_semantics",
                                          "request_filters_permissions_and_consumer", "live_orm_schema_and_manager",
                                          "execution_environment_contract"],
                           "action": "review_only_keep_original"})
    return {"schema_version": 1, "source_sha256": source_hash(source),
            "scope": "Offline class-config extraction for one audited mixin grammar",
            "candidates": candidates, "skipped": skipped}


def extract_registry(source):
    """Read the current declarative RelationSpec registry, without importing Django."""
    module = ast.parse(source)
    imports, rebound = imports_and_rebindings(module)
    assignments = [n for n in module.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "SPECS" for t in n.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Dict):
        raise ValueError("Registry is not one literal dictionary")
    result = {}
    for key, value in zip(assignments[0].value.keys, assignments[0].value.values):
        if not (isinstance(key, ast.Constant) and type(key.value) is str
                and isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "RelationSpec" and len(value.args) == 7 and not value.keywords):
            raise ValueError("Unsupported registry entry")
        a = value.args
        if (not isinstance(a[0], ast.Constant) or a[0].value != key.value
                or not isinstance(a[3], ast.Constant) or type(a[3].value) is not str
                or key.value in result):
            raise ValueError("Ambiguous registry identity/field")
        result[key.value] = {"key": key.value, "target": symbol(a[1], imports, rebound),
                             "links": symbol(a[2], imports, rebound), "relation_field": a[3].value,
                             "line": value.lineno, "registry_source_sha256": source_hash(source)}
    return result


def compare_to_registry(extraction, registry, audited_keys):
    """Audited attnames are historical cross-checks, not fresh runtime admission."""
    comparisons = []
    for candidate in extraction["candidates"]:
        config = candidate["configuration"]
        matches = [r for r in registry.values() if r["target"] == config["model"]
                   and r["links"] == config["document_count_through"] and r["target"] and r["links"]]
        row = {"class": candidate["class"], "action": "review_only_keep_original"}
        if candidate["status"] != "candidate" or len(matches) != 1:
            row["status"] = "unresolved_or_no_unique_registry_match"
        else:
            registered = matches[0]
            row.update(registry_key=registered["key"], symbolic_model_match=True,
                       registry_relation_field=registered["relation_field"],
                       extracted_helper_key=config["document_count_source_field"])
            metadata = audited_keys.get(registered["key"])
            if not metadata:
                row["status"] = "needs_audited_attname"
            else:
                row["historical_attname"] = metadata["group_key"]
                row["metadata_evidence"] = metadata["evidence"]
                row["status"] = ("matches_registered_relation" if config["document_count_source_field"] == metadata["group_key"]
                                 else "relation_key_mismatch")
        row["consumer_admission"] = "not_established"
        row["source_ordering"] = candidate["declared_ordering"]
        row["experiment_ordering"] = ["pk"]
        comparisons.append(row)
    return comparisons


def load_audited_attnames(directory, registry_source, model_source, helper_source):
    """Cross-check archived metadata only when its source lineage still matches."""
    manifest = json.loads((directory / "manifest.json").read_text())
    expected = {"inputs/tag_query_binding.py": source_hash(registry_source)}
    issues = []
    for filename, actual in expected.items():
        if manifest["files"].get(filename) != actual:
            issues.append("registry_revision_mismatch")
    for filename, content in (("models.py", model_source), ("permissions.py", helper_source)):
        key = "sources/paperless-ngx/src/documents/" + filename
        if manifest["source_hashes"].get(key) != source_hash(content):
            issues.append("model_or_helper_revision_mismatch:" + filename)
    if issues:
        return {}, issues
    attnames = {}
    for name in ("tag-query-binding.json", "tag-reuse.json"):
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"].get(name):
            return {}, ["metadata_artifact_hash_mismatch:" + name]
        records = json.loads(path.read_text())["records"]
        for index, record in enumerate(records):
            if record.get("kind") != "differential" or record.get("status") != "passed":
                continue
            relation = record["facts"]["relation_spec"]
            key = relation["key"]
            observed = relation["group_key"]
            if key in attnames and attnames[key]["group_key"] != observed:
                return {}, ["inconsistent_audited_attname:" + key]
            attnames[key] = {"group_key": observed,
                             "evidence": {"path": str(path), "sha256": manifest["files"][name],
                                          "record_index": index, "scope": "historical_metadata_only"}}
    return attnames, issues
