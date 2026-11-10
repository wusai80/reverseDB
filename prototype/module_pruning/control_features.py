"""Structured scalar def/use + control dependencies for finite Python fixtures.

This is a separate research extension; the frozen v1 effect extractor is intact.
No executions, labels, counterexamples or probability parameters are inputs.
Paths are conservative syntactic evidence, never proofs of deletion legality.
"""
import ast
from collections import deque


FEATURES = ["module_controls_observation", "module_may_return",
            "retained_action_same_guard_context"]


def loads(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def extract_control(source, input_domains):
    if set(input_domains) != {"x", "flag"}:
        raise ValueError("Only contractual x/flag domains accepted")
    if not input_domains["x"] or not input_domains["flag"]:
        raise ValueError("Empty input domain")
    if any(type(x) is not int for x in input_domains["x"]) or any(type(x) is not bool for x in input_domains["flag"]):
        raise ValueError("Primitive integer/boolean inputs required")
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execute"]
    if len(functions) != 1:
        raise ValueError("Exactly one execute function required")
    function = functions[0]
    args = function.args
    if (function.decorator_list or [a.arg for a in args.args] != ["x", "flag", "removed"] or args.posonlyargs
            or args.kwonlyargs or args.vararg or args.kwarg or args.defaults):
        raise ValueError("Expected execute(x, flag, removed)")
    if len(function.body) != 1 or not isinstance(function.body[0], ast.Try):
        raise ValueError("Expected one explicit outer observation wrapper")
    wrapper = function.body[0]
    expected_handler = ast.parse('try:\n pass\nexcept Rejected as error:\n return {"error": str(error)}').body[0].handlers[0]
    if (wrapper.finalbody or wrapper.orelse or len(wrapper.handlers) != 1
            or ast.dump(wrapper.handlers[0], include_attributes=False) != ast.dump(expected_handler, include_attributes=False)):
        raise ValueError("Unmodeled exception wrapper")
    rejected = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Rejected"]
    if (len(rejected) != 1 or ast.dump(rejected[0], include_attributes=False)
            != ast.dump(ast.parse('class Rejected(Exception):\n pass').body[0], include_attributes=False)):
        raise ValueError("Expected inert Rejected exception class")
    nodes = []; edges = set(); unknown = []
    syntax = {}; guard_contexts = {}; module_nodes = set(); observers = set()

    def unsupported(node, reason):
        record = dict(line=getattr(node, "lineno", None), reason=reason)
        if record not in unknown:
            unknown.append(record)

    # Do not silently specialize away input mutation, unknown control or hidden
    # exits. Unsupported statements reject this entire graph, preserving unknown.
    allowed = (ast.If, ast.Assign, ast.AugAssign, ast.Return, ast.Raise)
    markers = []
    def validate(block):
        for node in block:
            if not isinstance(node, allowed):
                raise ValueError("Unsupported control statement: " + type(node).__name__)
            if isinstance(node, ast.If):
                if "removed" in loads(node.test):
                    if ast.dump(node.test) != ast.dump(ast.parse("not removed", mode="eval").body) or node.orelse:
                        raise ValueError("Only exact if not removed region marker supported")
                    markers.append(node)
                validate(node.body); validate(node.orelse)
            elif "removed" in loads(node):
                raise ValueError("Deletion flag used outside region marker")
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], (ast.Name, ast.Subscript)):
                    raise ValueError("Unsupported assignment target")
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if not isinstance(target, (ast.Name, ast.Subscript)):
                    raise ValueError("Unsupported assignment target")
                if isinstance(target, ast.Name) and target.id in {"x", "flag", "removed", "Rejected"}:
                    raise ValueError("Input/exception symbol mutation unsupported")
    validate(wrapper.body)
    if len(markers) != 1:
        raise ValueError("Exactly one module region required")

    def inspect_expression(expr, env, context):
        if expr is None:
            return
        for part in ast.walk(expr):
            if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load):
                if part.id not in env and part.id not in {"x", "flag", "Rejected", "dict"}:
                    unsupported(part, "unbound_or_external_name")
            if isinstance(part, (ast.Subscript, ast.Attribute)):
                unsupported(part, "heap_or_external_access_not_summarized")
            if isinstance(part, ast.Call):
                is_rejection = (context == "raise" and part is expr and isinstance(part.func, ast.Name)
                                and part.func.id == "Rejected" and not part.keywords and len(part.args) == 1
                                and isinstance(part.args[0], ast.Constant) and type(part.args[0].value) is str)
                if not is_rejection:
                    unsupported(part, "call_effects_not_summarized")
            if isinstance(part, ast.BinOp) and not isinstance(part.op, (ast.Add, ast.Sub, ast.Mult)):
                unsupported(part, "implicit_arithmetic_exception_not_summarized")
            if isinstance(part, ast.IfExp) or (isinstance(part, ast.BoolOp) and context != "condition"):
                unsupported(part, "expression_level_control_not_summarized")
            if isinstance(part, (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom, ast.Lambda,
                                 ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                raise ValueError("Hidden expression control unsupported: " + type(part).__name__)

    def make(node, kind, env, controls, guards, in_module, reads):
        ident = len(nodes)
        nodes.append(dict(id=ident, line=node.lineno, kind=kind, syntax=ast.unparse(node), in_module=in_module))
        syntax[ident] = ast.dump(node, include_attributes=False)
        guard_contexts[ident] = guards
        if in_module:
            module_nodes.add(ident)
        for name in reads:
            for definition in env.get(name, set()):
                edges.add((definition, ident, "data"))
        for predicate in controls:
            edges.add((predicate, ident, "control"))
        if kind in {"return", "raise"}:
            observers.add(ident)
        return ident

    def merge(a, b):
        return {key: a.get(key, set()) | b.get(key, set()) for key in a.keys() | b.keys()}

    def block(statements, env, controls, guards=(), in_module=False):
        env = {k: set(v) for k, v in env.items()}
        controls = set(controls)
        for node in statements:
            if isinstance(node, ast.If):
                if node is markers[0]:
                    flow = block(node.body, env, controls, guards, True)
                    if flow is None:
                        return None
                    env, controls = flow
                    continue
                inspect_expression(node.test, env, "condition")
                reads = loads(node.test)
                ident = make(node, "branch", env, controls, guards, in_module, reads)
                # Syntactic guard context includes reaching definition identities;
                # same spelling after reassignment is not the same guard input.
                guard = (ast.dump(node.test, include_attributes=False),
                         tuple((name, tuple(sorted(env.get(name, set())))) for name in sorted(reads)))
                left = block(node.body, env, controls | {ident}, guards + ((guard, True),), in_module)
                right = block(node.orelse, env, controls | {ident}, guards + ((guard, False),), in_module)
                if left is None and right is None:
                    return None
                if left is None or right is None:
                    # An early exit makes subsequent execution depend on the
                    # surviving branch, even outside its lexical indentation.
                    env, controls = right if left is None else left
                else:
                    if left[0].keys() != right[0].keys():
                        unsupported(node, "conditionally_defined_scalar_may_be_unbound")
                    env = merge(left[0], right[0])
                    controls |= (left[1] | right[1]) - {ident}
                continue
            kind = "assign" if isinstance(node, ast.Assign) else "augment" if isinstance(node, ast.AugAssign) else "return" if isinstance(node, ast.Return) else "raise"
            expression = node.exc if isinstance(node, ast.Raise) else node.value
            if isinstance(node, ast.Raise) and (node.exc is None or node.cause is not None):
                unsupported(node, "exception_context_not_summarized")
            if isinstance(node, ast.AugAssign) and not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                unsupported(node, "implicit_arithmetic_exception_not_summarized")
            inspect_expression(expression, env, kind)
            reads = loads(expression) if expression is not None else set()
            target = None
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(target, ast.Subscript):
                    unsupported(target, "heap_write_alias_effects_not_summarized")
                    reads |= loads(target)
                elif isinstance(node, ast.AugAssign):
                    reads.add(target.id)
                if isinstance(expression, (ast.Dict, ast.List, ast.Set)):
                    unsupported(expression, "mutable_local_heap_not_summarized")
            ident = make(node, kind, env, controls, guards, in_module, reads)
            if kind in {"return", "raise"}:
                return None
            if isinstance(target, ast.Name):
                # Strong kill for scalar reaching definitions; branch merges
                # retain both incoming versions when overwrite is conditional.
                env[target.id] = {ident}
        return env, controls

    block(wrapper.body, {}, set())
    adjacency = {}
    for src, dst, kind in sorted(edges):
        adjacency.setdefault(src, []).append((dst, kind))
    paths = []
    for start in sorted(module_nodes):
        queue = deque([(start, False, [])]); visited = set()
        while queue:
            here, has_control, path = queue.popleft()
            if (here, has_control) in visited:
                continue
            visited.add((here, has_control))
            if here in observers and has_control:
                paths.append(dict(module_node=start, observation_node=here, edges=path))
                break
            for dst, kind in adjacency.get(here, []):
                queue.append((dst, has_control or kind == "control", path + [dict(source=here, target=dst, kind=kind)]))
    matches = []
    for src in sorted(module_nodes):
        if nodes[src]["kind"] == "branch":
            continue
        for dst in range(src+1, len(nodes)):
            if dst not in module_nodes and syntax[src] == syntax[dst] and guard_contexts[src] == guard_contexts[dst]:
                matches.append(dict(module_node=src, retained_node=dst,
                                    guard_context=guard_contexts[src], replacement_proven=False))
    raw = dict(module_controls_observation=bool(paths),
               module_may_return=any(nodes[i]["kind"] == "return" for i in module_nodes),
               retained_action_same_guard_context=bool(matches))
    # Partial graphs do not certify the absence of dependencies or matching
    # guard semantics. Preserve true positive syntactic evidence, otherwise None.
    features = {k: v if v or not unknown else None for k, v in raw.items()}
    return dict(status="partial" if unknown else "supported_scalar_structure", features=features,
                nodes=nodes, edges=[dict(source=a, target=b, kind=c) for a,b,c in sorted(edges)],
                control_paths=paths, retained_guard_matches=matches, unknown_effects=unknown,
                deletion_authorized=False, effect_summary_complete=False,
                scope="structured scalar dependencies and explicit return/raise observations; no feasibility, equivalence, implicit exception, heap or JDBC proof")
