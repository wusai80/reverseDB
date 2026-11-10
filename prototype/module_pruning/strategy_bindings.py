"""Restricted source-native constructor/slot binding facts, not deletion proof."""
import ast
import hashlib


class Unsupported(ValueError):
    pass


def _attribute(node, receiver):
    return node.attr if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==receiver else None


def _assignment(node):
    if isinstance(node,ast.AnnAssign):
        return node.target,node.value
    if isinstance(node,ast.Assign) and len(node.targets)==1:
        return node.targets[0],node.value
    raise Unsupported("unsupported_assignment")


def extract_bindings(source, roots, parameter_domains):
    """Only source, declared entrypoints and parameter facts are accepted."""
    result = dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),schema="source-strategy-dependencies/v1",
                  effect_summary_complete=False,deletion_authorized=False,
                  scope_assumptions=["exact local classes and declared entrypoints", "no external reflection, monkeypatching or concurrent receiver mutation", "ordinary input interfaces; no hidden receiver backreferences"],
                  unclosed_obligations=["external call effects", "other entrypoints and full application context", "probability calibration"])
    try:
        return dict(result,**_extract(source,roots,parameter_domains))
    except Unsupported as error:
        return dict(result,status="unknown",reason=str(error),bindings=[],edges=[],candidate_reachability=None)


def _extract(source, roots, domains):
    tree = ast.parse(source)
    if not isinstance(domains,dict) or any(not isinstance(v,list) or not v or any(type(x) is not bool for x in v) for v in domains.values()):
        raise Unsupported("Boolean parameter domains required")
    if not isinstance(roots,list) or not roots or any(set(r)!={"class","method"} for r in roots):
        raise Unsupported("explicit class/method roots required")
    classes = {}; methods = {}
    for statement in tree.body:
        if isinstance(statement,(ast.Import,ast.ImportFrom,ast.ClassDef)):
            continue
        if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Constant) and isinstance(statement.value.value,str):
            continue
        if (isinstance(statement,ast.If) and isinstance(statement.test,ast.Name) and statement.test.id=="TYPE_CHECKING"
                and not statement.orelse and all(isinstance(n,(ast.Import,ast.ImportFrom)) for n in statement.body)):
            continue
        raise Unsupported("module_initialization_outside_grammar")
    for cls in [n for n in tree.body if isinstance(n,ast.ClassDef)]:
        if cls.name in classes or cls.decorator_list or cls.keywords or len(cls.bases)>1:
            raise Unsupported("ambiguous_or_dynamic_class")
        if cls.bases and not isinstance(cls.bases[0],ast.Name):
            raise Unsupported("nonlocal_base_expression")
        classes[cls.name] = cls
        entries = {}
        for method in cls.body:
            if isinstance(method,ast.Expr) and isinstance(method.value,ast.Constant) and isinstance(method.value.value,str):
                continue
            if not isinstance(method,ast.FunctionDef) or method.decorator_list or method.name in entries:
                raise Unsupported("descriptor_or_class_body_outside_grammar")
            if method.name in {"__getattribute__","__getattr__","__setattr__","__delattr__"}:
                raise Unsupported("dynamic_attribute_hook")
            if not method.args.args or method.args.args[0].arg!="self":
                raise Unsupported("ordinary_self_receiver_required")
            if any(not isinstance(value,ast.Constant) for value in method.args.defaults+list(filter(lambda x:x is not None,method.args.kw_defaults))):
                raise Unsupported("effectful_default_argument")
            if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)) for statement in method.body for n in ast.walk(statement)):
                raise Unsupported("nested_callable_scope")
            entries[method.name] = method
        methods[cls.name] = entries

    # Inherited attribute hooks/metaclasses can affect even locally defined
    # methods. Validate the entire ancestry, not only lookup paths used below.
    for start in classes:
        current=start; seen=set()
        while True:
            if current in seen:
                raise Unsupported("inheritance_cycle")
            if current not in classes:
                raise Unsupported("external_base_class")
            seen.add(current)
            bases=classes[current].bases
            if not bases:
                break
            current=bases[0].id

    def resolve(cls, method):
        visited = set()
        while cls in classes:
            if cls in visited:
                raise Unsupported("inheritance_cycle")
            visited.add(cls)
            if method in methods[cls]:
                return cls+"."+method,methods[cls][method]
            bases = classes[cls].bases
            if not bases:
                break
            cls = bases[0].id
        raise Unsupported("unresolved_method:"+cls+"."+method)

    parents = {child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if (isinstance(node,ast.Name) and node.id=="self" and isinstance(node.ctx,(ast.Store,ast.Del))) or (isinstance(node,ast.ExceptHandler) and node.name=="self"):
            raise Unsupported("receiver_reassigned")
        if isinstance(node,ast.Call):
            name = node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else None
            if name in {"getattr","setattr","delattr","vars","eval","exec","super","__getattribute__","__setattr__","__delattr__"}:
                raise Unsupported("dynamic_attribute_access")
        if isinstance(node,ast.Attribute) and node.attr in {"__dict__","__class__"}:
            raise Unsupported("dynamic_attribute_dictionary")
        if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id=="self":
            parent = parents.get(node)
            if not isinstance(parent,ast.Attribute) or parent.value is not node:
                raise Unsupported("receiver_escape")

    bindings=[]; edges=[]; nodes={}; external=[]; reachable=set(); all_definitions={}
    for cls,entries in methods.items():
        for name,method in entries.items():
            all_definitions[cls+"."+name]=dict(line=method.lineno,end=method.end_lineno)
    accepted_domain_names=set()
    for root in roots:
        concrete,entry=root["class"],root["method"]
        if concrete not in classes:
            raise Unsupported("unknown_root_class")
        init_id,constructor=resolve(concrete,"__init__")
        if constructor.args.vararg or constructor.args.kwarg:
            raise Unsupported("variadic_constructor")
        statements=[n for n in constructor.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str))]
        selectors=[n for n in statements if isinstance(n,ast.If)]
        if len(selectors)!=1 or not isinstance(selectors[0].test,ast.Name):
            raise Unsupported("single_Boolean_selector_required")
        selector=selectors[0]; parameter=selector.test.id
        args=constructor.args
        positional=args.posonlyargs+args.args
        parameters={a.arg for a in positional+args.kwonlyargs}
        if parameter not in parameters or parameter=="self":
            raise Unsupported("selector_not_parameter")
        if any(isinstance(n,ast.Name) and n.id==parameter and isinstance(n.ctx,(ast.Store,ast.Del)) for n in ast.walk(constructor)):
            raise Unsupported("selector_reassigned")
        accepted_domain_names.add(parameter)
        values=sorted(set(domains.get(parameter,[False,True])))
        supplied=parameter in domains
        defaults=dict(zip([a.arg for a in positional][-len(args.defaults):],args.defaults)) if args.defaults else {}
        defaults.update({a.arg:value for a,value in zip(args.kwonlyargs,args.kw_defaults) if value is not None})
        default=defaults.get(parameter)
        default_value=default.value if isinstance(default,ast.Constant) and type(default.value) is bool else None
        targets={}; writes=[]; binding_references=[]; slot=None
        for truth,branch in [(True,selector.body),(False,selector.orelse)]:
            if len(branch)!=1:
                raise Unsupported("single_binding_per_branch_required")
            target,value=_assignment(branch[0])
            this_slot=_attribute(target,"self"); target_method=_attribute(value,"self")
            if this_slot is None or target_method is None or (slot is not None and slot!=this_slot):
                raise Unsupported("same_slot_bound_self_methods_required")
            slot=this_slot; writes.append(target); binding_references.append(value)
            target_id,target_node=resolve(concrete,target_method)
            targets[truth]=dict(definition=target_id,line=branch[0].lineno,lookup_line=value.lineno,method=target_method)
            nodes[target_id]=dict(line=target_node.lineno,end=target_node.end_lineno)
        if any(slot in entries for entries in methods.values()):
            raise Unsupported("slot_conflicts_with_class_method")
        for statement in statements:
            if statement is selector:
                continue
            target,value=_assignment(statement)
            if (_attribute(target,"self") is None or not isinstance(value,ast.Name)
                    or value.id not in parameters or _attribute(target,"self")==slot):
                raise Unsupported("constructor_statement_outside_grammar")
        method_names={name for entries in methods.values() for name in entries}
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and isinstance(node.ctx,(ast.Store,ast.Del)):
                if node.attr==slot and node not in writes:
                    raise Unsupported("binding_slot_mutated")
                if node.attr in method_names:
                    raise Unsupported("method_shadowed")
            if isinstance(node,ast.Attribute) and _attribute(node,"self") in method_names|{slot} and isinstance(node.ctx,ast.Load):
                parent=parents.get(node)
                if node not in binding_references and not (isinstance(parent,ast.Call) and parent.func is node):
                    raise Unsupported("bound_method_escape")
        root_id=concrete+"::construct_then_"+entry
        nodes[root_id]=dict(root=True)
        nodes[init_id]=dict(line=constructor.lineno,end=constructor.end_lineno)
        edges.append(dict(source=root_id,target=init_id,kind="construct",root_class=concrete))
        reachable.add(init_id)
        selected=[targets[value] for value in values]
        for truth in values:
            target=targets[truth]
            edges.append(dict(source=init_id,target=target["definition"],kind="bind_time_lookup",root_class=concrete,
                              line=target["lookup_line"],guard=dict(parameter=parameter,value=truth)))
            reachable.add(target["definition"])
        bindings.append(dict(root_class=concrete,constructor=init_id,parameter=parameter,slot=slot,
                             domain=values,context_origin="explicit_contract" if supplied else "unknown_caller_option",
                             declared_default=default_value,default_used_to_narrow=False,
                             targets={str(key).lower():value for key,value in targets.items()},
                             selected_definitions=[t["definition"] for t in selected]))
        entry_id,_=resolve(concrete,entry)
        edges.append(dict(source=root_id,target=entry_id,kind="entry_invocation",root_class=concrete))
        pending=[entry_id]; visited=set()
        # Construction looks up methods even if there are no documents. Body
        # calls are followed separately, only from declared method entrypoints.
        while pending:
            current=pending.pop()
            if current in visited:
                continue
            visited.add(current); reachable.add(current)
            owner,name=current.split(".",1); method=methods[owner][name]
            nodes[current]=dict(line=method.lineno,end=method.end_lineno)
            for call in [n for statement in method.body for n in ast.walk(statement) if isinstance(n,ast.Call)]:
                called=_attribute(call.func,"self")
                if called==slot:
                    for truth in values:
                        target=targets[truth]["definition"]
                        edges.append(dict(source=current,target=target,kind="bound_slot_call",line=call.lineno,slot=slot,
                                          root_class=concrete,guard=dict(parameter=parameter,value=truth)))
                        pending.append(target)
                elif called is not None and called in method_names:
                    target,_=resolve(concrete,called)
                    edges.append(dict(source=current,target=target,kind="direct_self_call",line=call.lineno,root_class=concrete))
                    pending.append(target)
                else:
                    external.append(dict(caller=current,line=call.lineno,callee=ast.unparse(call.func),root_class=concrete,status="unknown_effects"))
    if set(domains)-accepted_domain_names:
        raise Unsupported("undeclared_parameter_fact")
    return dict(status="binding_facts_under_declared_scope",bindings=bindings,nodes=nodes,edges=edges,
                candidate_reachability={name:name in reachable for name in sorted(all_definitions)},
                method_definitions=all_definitions,external_calls=external,
                interpretation="Structural may-reach including constructor lookup, not executed frequency, full effect closure or deletion permission")
