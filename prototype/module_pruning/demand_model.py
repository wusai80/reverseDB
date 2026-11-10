"""Three-way demand analysis on supplied finite graphs, NOT Java/SQL analysis.

Known paths require retention. Possible paths or incomplete obligations mean
unknown. Absence of paths permits only a conditional deletion candidate.
"""
from collections import deque


OBLIGATIONS = ("source_binding_valid", "dependency_summary_complete",
               "scope_assumptions_declared", "boundary_edits_valid")


def guard(item, facts):
    unknown = False
    for key, wanted in item.get("when", {}).items():
        if not isinstance(wanted, bool):
            raise ValueError("Only Boolean condition literals are supported")
        value = facts.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError("Facts must be Boolean or unknown")
        if value is None:
            unknown = True
        elif value != wanted:
            return False
    return None if unknown else True


def reachable(graph, facts, possible):
    nodes = set(graph["nodes"])
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Duplicate graph nodes")
    adjacency = {node: [] for node in nodes}
    for edge in graph["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise ValueError("Unknown edge endpoint")
        active = guard(edge, facts)
        if active is True or (possible and active is None):
            adjacency[edge["from"]].append(edge)
    paths = {}
    queue = deque()
    for root in graph["roots"]:
        if root["node"] not in nodes:
            raise ValueError("Unknown root")
        active = guard(root, facts)
        if active is True or (possible and active is None):
            node = root["node"]
            if node not in paths:
                paths[node] = [{"root":node,"guard":active}]
                queue.append(node)
    while queue:
        node = queue.popleft()
        for edge in adjacency[node]:
            target = edge["to"]
            if target not in paths:
                paths[target] = paths[node] + [{**edge, "guard":guard(edge,facts)}]
                queue.append(target)
    return paths


def decide(graph, facts, removed, obligations):
    removed = set(removed)
    if not removed or not removed <= set(graph["nodes"]):
        raise ValueError("Invalid removal set")
    known = reachable(graph, facts, False)
    possible = reachable(graph, facts, True)
    required = sorted(removed & known.keys())
    unresolved = sorted(removed & possible.keys())
    if required:
        return dict(decision="retain",reason="required_dependency",witnesses={n:known[n] for n in required})
    if unresolved:
        return dict(decision="unknown",reason="possible_dependency",witnesses={n:possible[n] for n in unresolved})
    for key in OBLIGATIONS:
        if obligations.get(key) is not True:
            # False means an invalid/unsupported certificate, not a theorem that the
            # business module is inherently necessary. Either case prevents deletion.
            return dict(decision="unknown",reason="unclosed_obligation",obligation=key)
    return dict(decision="conditionally_deletable",reason="outside_demand_closure",
                assumptions="supplied graph is conservative and complete under declared scope; boundary edits valid")
