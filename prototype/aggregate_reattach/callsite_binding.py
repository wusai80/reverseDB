"""Bind extracted configuration to the existing audited region experiment.

This does not admit a ViewSet consumer or recover its runtime MRO. The caller
supplies the existing pk-ordered region query and its original fallback.
"""
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

from documents import models
from callsite_extraction import extract_callsites, source_hash
import tag_query_binding as runtime


VIEWS = Path(models.__file__).with_name("views.py")
VIEWS_SHA256 = "07c836f31036a98105866035620fbe576559d95d344c1734cdd5d1774852e270"
CALLSITES = {"TagViewSet", "CustomFieldViewSet"}


@dataclass
class CallsiteBinding:
    plan: dict
    facts: dict
    extracted: dict
    spec_key: str | None = None
    binding: runtime.Binding | None = None
    fingerprint: str | None = None
    used: bool = False


@lru_cache(maxsize=1)
def _parse_source(source):
    """Cache a pure syntax result by exact text, never admission/runtime facts.

    Serialized output cannot be mutated by callers. Keep only one source version;
    errors are not cached. _resolve still reads and verifies the file every time.
    """
    return json.dumps(extract_callsites(source))


def _resolve(extracted):
    source = VIEWS.read_text()
    runtime._require(source_hash(source) == VIEWS_SHA256, "callsite source revision changed")
    runtime._require(isinstance(extracted, dict) and extracted.get("class") in CALLSITES,
                     "unknown callsite configuration")
    matches = [c for c in json.loads(_parse_source(source))["candidates"]
               if c["class"] == extracted["class"]]
    runtime._require(len(matches) == 1 and matches[0] == extracted
                     and extracted.get("status") == "candidate",
                     "candidate differs from current source extraction")
    # Explicit symbol boundary; never import/eval paths supplied by a candidate.
    symbols = {
        "documents.models.Tag": models.Tag,
        "documents.models.CustomField": models.CustomField,
        "documents.models.Document.tags.through": models.Document.tags.through,
        "documents.models.CustomFieldInstance": models.CustomFieldInstance,
    }
    config = extracted["configuration"]
    target = symbols.get(config["model"])
    links = symbols.get(config["document_count_through"])
    runtime._require(target is not None and links is not None, "unresolved model symbol")
    # Resolve by real attname, not by stripping an _id suffix.
    fields = [f for f in links._meta.concrete_fields
              if f.attname == config["document_count_source_field"]
              and f.is_relation and f.related_model is target]
    runtime._require(len(fields) == 1, "helper key has no unique target relation")
    field = fields[0]
    policies = [s for s in runtime.SPECS.values() if s.target_model is target
                and s.link_model is links and s.relation_field == field.name]
    runtime._require(len(policies) == 1, "no unique audited relation policy")
    policy = policies[0]
    return policy.key, {
        "callsite": extracted["class"], "source_sha256": source_hash(source),
        "configuration": config, "resolved_target": target._meta.label,
        "resolved_links": links._meta.label, "relation_field": field.name,
        "helper_attname": field.attname, "audited_policy": policy.key,
        "source_ordering": extracted["declared_ordering"],
        "consumer_admission": "not_established",
        "experiment_consumer": runtime.CONSUMER,
    }


def bind_callsite(extracted, user, target, declarations, consumer=runtime.CONSUMER):
    snapshot = deepcopy(extracted)
    try:
        key, facts = _resolve(snapshot)
        bound = runtime.bind_count_query(key, user, target, declarations, consumer)
        facts["runtime"] = bound.facts
        fingerprint = runtime.digest({"extracted": snapshot, "facts": facts})
        return CallsiteBinding(bound.plan, facts, snapshot, key, bound,
                               fingerprint if bound.candidate is not None else None)
    except (runtime.UnboundEntry, OSError, SyntaxError) as exc:
        return CallsiteBinding({"decision": "rejected", "action": "keep_original",
                                "reason": str(exc)}, {}, snapshot)


def execute_callsite(candidate, user, target, declarations, *, fallback,
                     consumer=runtime.CONSUMER):
    """One use; validate provenance again, then use the existing runtime guard.

    fallback is the caller's original region operation, independent of untrusted
    extracted data. Execution errors propagate; no retry after SQL has started.
    """
    def original(reason):
        return fallback(), {"route": "original", "reason": reason}

    if candidate.used or candidate.fingerprint is None or candidate.binding is None:
        return original("missing_or_used_callsite_candidate")
    candidate.used = True
    try:
        key, facts = _resolve(candidate.extracted)
    except (runtime.UnboundEntry, OSError, SyntaxError):
        return original("callsite_or_runtime_binding_changed")
    # Validate source provenance and the retained token here. The executor below
    # performs the single fresh ORM/schema/context rebind. Do not reuse schema
    # facts as current evidence, cache them, or execute the retained SQL payload.
    facts["runtime"] = candidate.binding.facts
    token = candidate.binding.candidate
    if (key != candidate.spec_key
            or runtime.digest({"extracted": candidate.extracted, "facts": facts}) != candidate.fingerprint
            or token is None or token.fingerprint != runtime.digest(candidate.binding.facts)):
        return original("callsite_or_runtime_binding_changed")
    return runtime.execute_count_candidate(key, candidate.binding, user,
                                           target, declarations, consumer, fallback=fallback)
