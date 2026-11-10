"""Bounded algebra checks, not a SQL engine or a program-equivalence proof."""
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = json.loads((HERE / "validation_spec.json").read_text())


def app_map(target, links, allowed):
    groups = {}
    for key, document in links:
        if key in target and document in allowed:
            groups.setdefault(key, set()).add(document)
    mapping = {key: len(documents) for key, documents in groups.items()}
    return [(key, mapping.get(key, 0)) for key in target]


def relational(target, links, allowed):
    # Independent group construction; target occurrences are retained by the join.
    distinct_pairs = {(key, doc) for key, doc in links if doc in allowed}
    grouped = Counter(key for key, doc in distinct_pairs)
    return [(key, grouped.get(key, 0)) for key in target]


def check_equal(left, right, context):
    if left != right:
        raise AssertionError(dict(context=context, original=left, candidate=right))


def run():
    model = SPEC["finite_model"]
    keys, documents = model["keys"], model["documents"]
    pairs = list(itertools.product(keys, documents))
    targets = [list(t) for n in range(model["target_sequence_max_length"] + 1)
               for t in itertools.product(keys, repeat=n)]
    allowed_sets = [set(d for d, included in zip(documents, bits) if included)
                    for bits in itertools.product((False, True), repeat=len(documents))]
    checked_p1 = checked_p2 = 0
    for multiplicities in itertools.product(model["link_multiplicities"], repeat=len(pairs)):
        links = [pair for pair, count in zip(pairs, multiplicities) for _ in range(count)]
        for target, allowed in itertools.product(targets, allowed_sets):
            original = app_map(target, links, allowed)
            p1 = relational(target, links, allowed)
            check_equal(original, p1, (target, links, sorted(allowed)))
            checked_p1 += 1
            for offset, limit in itertools.product(model["offsets"], model["limits"]):
                window = target[offset:offset + limit]
                demand = set(window)
                restricted = [(key, doc) for key, doc in links if key in demand]
                expected = original[offset:offset + limit]
                # P2 alone keeps application materialization; composition removes it.
                p2 = app_map(window, restricted, allowed)
                combined = relational(window, restricted, allowed)
                check_equal(expected, p2, ("P2", target, offset, limit))
                check_equal(expected, combined, ("P1+P2", target, offset, limit))
                checked_p2 += 1

    counterexamples = []

    def witness(rule, inputs, original, mutant):
        if original == mutant:
            raise AssertionError(f"Mutation survived: {rule}")
        counterexamples.append(dict(rule=rule, inputs=inputs, original=original, mutant=mutant))

    target, links, allowed = [0], [], {0}
    witness("inner_join_drops_empty_group", dict(target=target, links=links),
            app_map(target, links, allowed),
            [(key, count) for key, count in relational(target, links, allowed) if count > 0])
    links = [(0, 0), (0, 0)]
    witness("count_without_distinct", dict(target=target, links=links),
            app_map(target, links, allowed),
            [(key, sum(k == key and d in allowed for k, d in links)) for key in target])
    target, links = [0, 1], [(1, 0)]
    all_counts = app_map(target, links, allowed)
    # The unmodified consumer has a deterministic key tie-breaker in this fixture.
    original_top = sorted(all_counts, key=lambda row: (-row[1], row[0]))[:1]
    premature = relational(target[:1], [], allowed)
    witness("page_before_count_dependent_sort", dict(target=target, links=links, limit=1),
            original_top, premature)
    demand_counts = app_map(target, [], allowed)
    witness("ignoring_global_consumer", dict(target=target, links=links, page_keys=[0]),
            dict(page=all_counts[:1], total=sum(c for _, c in all_counts)),
            dict(page=demand_counts[:1], total=sum(c for _, c in demand_counts)))

    return dict(
        status="passed", scope="bounded mathematical model only",
        p1_instances=checked_p1, p2_instances=checked_p2,
        composition_instances=checked_p2, counterexamples=counterexamples,
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (Path(__file__), HERE / "validation_spec.json")},
        pending=SPEC["pending_database_checks"], exclusions=model["exclusions"],
    )


if __name__ == "__main__":
    report = run()
    output = ROOT / "evidence/app-db-questions/20261104/model-check.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "p1_instances", "p2_instances", "composition_instances")}))
    print(f"Counterexamples: {len(report['counterexamples'])}; report: {output}")
