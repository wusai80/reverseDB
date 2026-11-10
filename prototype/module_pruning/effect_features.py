"""Static features for the frozen finite fixture language, not a Python analyzer.

Input is source + program configuration + contractual domains. No oracle labels,
counterexamples or program execution are accepted. Source bindings are explicit.
"""
import ast
import copy
import operator


UNKNOWN=object()


def value(node,bindings):
    if isinstance(node,ast.Constant): return node.value
    if isinstance(node,ast.Name): return bindings.get(node.id,UNKNOWN)
    if isinstance(node,(ast.Tuple,ast.List)):
        values=[value(n,bindings) for n in node.elts]
        return UNKNOWN if any(v is UNKNOWN for v in values) else tuple(values)
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):
        v=value(node.operand,bindings); return UNKNOWN if v is UNKNOWN else not v
    if isinstance(node,ast.Compare) and len(node.ops)==1:
        a,b=value(node.left,bindings),value(node.comparators[0],bindings)
        if a is UNKNOWN or b is UNKNOWN: return UNKNOWN
        operations={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
        return operations[type(node.ops[0])](a,b) if type(node.ops[0]) in operations else UNKNOWN
    return UNKNOWN


def expression(node,bindings):
    if isinstance(node,ast.IfExp):
        test=expression(node.test,bindings); known=value(test,bindings)
        if known is not UNKNOWN: return expression(node.body if known else node.orelse,bindings)
    if isinstance(node,ast.BoolOp):
        items=[expression(n,bindings) for n in node.values]
        # Conditions in this supported fixture grammar are pure comparisons/names.
        if any(isinstance(n,(ast.Call,ast.NamedExpr,ast.Await,ast.Yield)) for item in items for n in ast.walk(item)):
            raise ValueError("Effectful condition outside feature grammar")
        is_and=isinstance(node.op,ast.And)
        for item in items:
            known=value(item,bindings)
            if known is not UNKNOWN and bool(known)!=is_and: return ast.copy_location(ast.Constant(not is_and),node)
        items=[n for n in items if value(n,bindings) is UNKNOWN]
        if not items: return ast.copy_location(ast.Constant(is_and),node)
        if len(items)==1: return items[0]
        return ast.copy_location(ast.BoolOp(op=node.op,values=items),node)
    result=copy.deepcopy(node)
    for field,item in ast.iter_fields(result):
        if isinstance(item,ast.expr): setattr(result,field,expression(item,bindings))
        elif isinstance(item,list): setattr(result,field,[expression(x,bindings) if isinstance(x,ast.expr) else x for x in item])
    known=value(result,bindings)
    if known is not UNKNOWN and isinstance(known,(str,bool,int,float)):
        return ast.copy_location(ast.Constant(known),node)
    return result


def statements(nodes,bindings):
    result=[]
    for node in nodes:
        if isinstance(node,ast.If):
            test=expression(node.test,bindings); known=value(test,bindings)
            if known is not UNKNOWN:
                part=statements(node.body if known else node.orelse,bindings)
            else:
                part=[ast.copy_location(ast.If(test=test,body=statements(node.body,bindings),orelse=statements(node.orelse,bindings)),node)]
        elif isinstance(node,(ast.Assign,ast.AugAssign,ast.Return,ast.Raise)):
            updated=copy.deepcopy(node)
            if hasattr(updated,"value") and updated.value is not None: updated.value=expression(updated.value,bindings)
            if isinstance(updated,ast.Raise) and updated.exc is not None: updated.exc=expression(updated.exc,bindings)
            if isinstance(updated,ast.Assign) and len(updated.targets)==1 and isinstance(updated.targets[0],ast.Tuple) and isinstance(updated.value,ast.Tuple):
                if len(updated.targets[0].elts)!=len(updated.value.elts): raise ValueError("Tuple arity mismatch")
                part=[ast.copy_location(ast.Assign(targets=[t],value=v),node) for t,v in zip(updated.targets[0].elts,updated.value.elts)]
            else: part=[updated]
        else:
            raise ValueError("Unsupported fixture statement: "+type(node).__name__)
        result.extend(part)
        if result and isinstance(result[-1],(ast.Return,ast.Raise)): break
    return result


def names(node): return {n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}


def target(node):
    t=node.targets[0] if isinstance(node,ast.Assign) and len(node.targets)==1 else node.target if isinstance(node,ast.AugAssign) else None
    if isinstance(t,ast.Name): return t.id,False
    if isinstance(t,ast.Subscript) and isinstance(t.value,ast.Name): return t.value.id,True
    raise ValueError("Unsupported mutation target")


def extract(source,static_bindings,input_domains):
    if set(static_bindings)!={"family","v"} or set(input_domains)!={"x","flag"}:
        raise ValueError("Only explicit program bindings and domains accepted")
    if any(not values for values in input_domains.values()): raise ValueError("Empty contract domain")
    bindings=dict(static_bindings)
    for key,values in input_domains.items():
        if len(set(values))==1: bindings[key]=values[0]
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=="execute")
    body=next(n.body for n in function.body if isinstance(n,ast.Try))
    specialized=statements(body,bindings)
    module=[]; retained_after=[]; ordinary=[]; returns=[]; seen_module=False
    def walk(nodes,in_module=False):
        nonlocal seen_module
        for n in nodes:
            if isinstance(n,ast.If):
                marker="removed" in names(n.test)
                if marker: seen_module=True
                if marker and n.orelse: raise ValueError("Deletion-control else unsupported")
                walk(n.body,in_module or marker); walk(n.orelse,in_module)
            elif isinstance(n,(ast.Assign,ast.AugAssign,ast.Raise)):
                if in_module: module.append(n)
                else:
                    ordinary.append((n,seen_module))
                    if seen_module: retained_after.append(n)
            elif isinstance(n,ast.Return): returns.append(n)
    walk(specialized)
    writes=[n for n in module if isinstance(n,(ast.Assign,ast.AugAssign))]
    raises=[n for n in module if isinstance(n,ast.Raise)]
    dependencies={}; aliases={}; initial_constants={}
    for n,after in ordinary:
        if not isinstance(n,ast.Assign): continue
        name,heap=target(n)
        if heap: continue
        val=n.value
        if isinstance(val,ast.Call) and isinstance(val.func,ast.Name) and val.func.id=="dict":
            if after: raise ValueError("Post-module copying requires temporal analysis")
            dependencies[name]=set() # pre-module snapshot does not alias future mutations
        else: dependencies[name]=names(val)
        if isinstance(val,ast.Name): aliases[name]=val.id
        else: aliases.pop(name,None)
        if not after and isinstance(val,ast.Constant): initial_constants[name]=val.value
    observed=set().union(*(names(r.value) for r in returns)) if returns else set()
    changed=True
    while changed:
        before=set(observed)
        for name in list(observed): observed.update(dependencies.get(name,set()))
        changed=observed!=before
    def root(name):
        seen=set()
        while name in aliases:
            if name in seen: raise ValueError("Alias cycle unsupported")
            seen.add(name); name=aliases[name]
        return name
    roots={root(n) for n in observed}
    visible=[]; through_alias=[]; neutral=[]
    for n in writes:
        name,heap=target(n)
        is_visible=root(name) in roots if heap else name in observed
        visible.append(is_visible)
        through_alias.append(heap and is_visible and any(a!=name and root(a)==root(name) for a in observed))
        neutral.append((isinstance(n,ast.AugAssign) and isinstance(n.op,ast.Add) and isinstance(n.value,ast.Constant) and n.value.value==0)
            or (isinstance(n,ast.Assign) and not heap and isinstance(n.value,ast.Constant) and name in initial_constants and n.value.value==initial_constants[name]))
    fingerprint=lambda n:ast.dump(n,include_attributes=False)
    retained={fingerprint(n) for n in retained_after}
    features=dict(module_may_write=bool(writes),module_may_raise=bool(raises),observable_write=any(visible),
        alias_write_to_observation=any(through_alias),neutral_update_only=bool(writes) and all(neutral) and not raises,
        retained_similar_action=any(fingerprint(n) in retained for n in module))
    return dict(features=features,module_actions=[dict(line=n.lineno,syntax=ast.unparse(n)) for n in module],
        retained_actions=[dict(line=n.lineno,syntax=ast.unparse(n)) for n in retained_after],observed_bindings=sorted(observed),
        specialized_source=ast.unparse(ast.fix_missing_locations(ast.Module(body=specialized,type_ignores=[]))),
        scope="finite fixture AST with explicit source bindings; syntactic similarity is not a replacement proof; not a Java effect summary")
