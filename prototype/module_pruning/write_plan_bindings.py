"""Conservative source-native guarded writes and write-list relationships."""
import ast
import hashlib


class Unsupported(ValueError):
    pass


def definition(nodes, kind, name):
    matches=[n for n in nodes if isinstance(n,kind) and n.name==name]
    if len(matches)!=1:
        raise Unsupported("ambiguous_definition:"+name)
    return matches[0]


def attr(node, receiver):
    return node.attr if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==receiver else None


def arguments(function):
    if function.decorator_list or function.args.posonlyargs or function.args.vararg or function.args.kwarg or function.args.kwonlyargs:
        raise Unsupported("ordinary_positional_function_required")
    return [a.arg for a in function.args.args]


def extract_write_plan(source, dto_source, binding, domains):
    base=dict(schema="source-write-plan-dependencies/v1",source_sha256=hashlib.sha256(source.encode()).hexdigest(),
              dto_sha256=hashlib.sha256(dto_source.encode()).hexdigest(),effect_summary_complete=False,deletion_authorized=False,
              scope_assumptions=["ordinary exact DTO fields and declared roles", "no hidden DTO mutation or monkeypatching", "structural may-flow, not path feasibility"],
              unclosed_obligations=["external/object call effects", "identity of equal queue members", "caller branches and database transactions/triggers/permissions"])
    try:
        return dict(base,**_extract(source,dto_source,binding,domains))
    except Unsupported as error:
        return dict(base,status="unknown",reason=str(error),writes=[],guard_regions=[],caller_link=None)


def _extract(source,dto_source,binding,domains):
    expected={"helper","dto_class","dto_parameter","object_parameter","queue_parameter","exclusion_parameter","caller","bulk_wrapper"}
    if set(binding)!=expected:
        raise Unsupported("explicit_source_roles_required")
    tree=ast.parse(source)
    helper=definition(tree.body,ast.FunctionDef,binding["helper"])
    formals=arguments(helper)
    payload,obj,queue,excluded=[binding[k] for k in ["dto_parameter","object_parameter","queue_parameter","exclusion_parameter"]]
    roles={payload,obj,queue,excluded}
    if len(roles)!=4 or not roles.issubset(formals):
        raise Unsupported("distinct_existing_roles_required")
    dto=definition(ast.parse(dto_source).body,ast.ClassDef,binding["dto_class"])
    if dto.bases or dto.keywords or [ast.unparse(d) for d in dto.decorator_list]!=["dataclass"] or any(not isinstance(n,ast.AnnAssign) for n in dto.body):
        raise Unsupported("ordinary_dataclass_required")
    bool_fields={n.target.id for n in dto.body if isinstance(n.target,ast.Name) and isinstance(n.annotation,ast.Name) and n.annotation.id=="bool"}
    if not isinstance(domains,dict) or set(domains)-bool_fields or any(not isinstance(v,list) or not v or any(type(x) is not bool for x in v) for v in domains.values()):
        raise Unsupported("Boolean_contract_fields_only")
    parents={child:parent for parent in ast.walk(helper) for child in ast.iter_child_nodes(parent)}

    def is_append(node):
        return (isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and attr(node.value.func,queue)=="append"
                and len(node.value.args)==1 and isinstance(node.value.args[0],ast.Name) and node.value.args[0].id==obj and not node.value.keywords)

    def is_membership(test, container):
        return (isinstance(test,ast.Compare) and isinstance(test.left,ast.Name) and test.left.id==obj
                and len(test.ops)==1 and isinstance(test.ops[0],(ast.In,ast.NotIn))
                and len(test.comparators)==1 and isinstance(test.comparators[0],ast.Name) and test.comparators[0].id==container)

    # Reject unsupported control up front; AST walking must not cross a hidden
    # function/loop or mistake a re-bound parameter for the original role.
    def validate(block):
        for node in block:
            if isinstance(node,ast.If):
                if any(isinstance(n,ast.Call) for n in ast.walk(node.test)):
                    raise Unsupported("effectful_condition")
                validate(node.body); validate(node.orelse)
            elif not isinstance(node,(ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Expr)):
                raise Unsupported("unsupported_helper_control:"+type(node).__name__)
    validate(helper.body)
    for node in ast.walk(helper):
        if isinstance(node,(ast.Lambda,ast.NamedExpr,ast.IfExp,ast.Await,ast.Yield,ast.YieldFrom)):
            raise Unsupported("hidden_expression_control")
        if isinstance(node,ast.Name) and node.id in formals and isinstance(node.ctx,(ast.Store,ast.Del)):
            raise Unsupported("parameter_rebound")
        if isinstance(node,ast.Attribute) and attr(node,payload) is not None and isinstance(node.ctx,(ast.Store,ast.Del)):
            raise Unsupported("DTO_mutated")
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name) and node.func.id in {"getattr","setattr","delattr","vars","eval","exec"}:
                raise Unsupported("reflection")
            if attr(node.func,queue) is not None and not is_append(parents.get(node)):
                raise Unsupported("unexpected_queue_mutation")
        if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id in roles:
            parent=parents.get(node)
            allowed=isinstance(parent,ast.Attribute) and parent.value is node and node.id in {payload,obj,queue}
            allowed=allowed or (isinstance(parent,ast.Compare) and (is_membership(parent,queue) or is_membership(parent,excluded)))
            allowed=allowed or (isinstance(parent,ast.Call) and is_append(parents.get(parent)) and node in parent.args)
            if not allowed:
                raise Unsupported("role_alias_or_escape:"+node.id)

    def truths(test):
        field=attr(test,payload)
        if field in bool_fields:
            return set(domains.get(field,[False,True]))
        if isinstance(test,ast.Constant) and type(test.value) is bool:
            return {test.value}
        if isinstance(test,ast.UnaryOp) and isinstance(test.op,ast.Not):
            return {not x for x in truths(test.operand)}
        if isinstance(test,ast.BoolOp):
            values={True} if isinstance(test.op,ast.And) else {False}
            for operand in test.values:
                values={a and b if isinstance(test.op,ast.And) else a or b for a in values for b in truths(operand)}
            return values
        return {False,True}

    def reachable(guards):
        return all(guard["taken_branch"] in guard["possible_values"] for guard in guards)

    def enqueue(future):
        if future and is_append(future[0]):
            return dict(kind="direct_append",line=future[0].lineno,same_instance_on_append=True,
                        same_instance_if_append_skipped=None,normal_completion_only=True)
        if future and isinstance(future[0],ast.If):
            condition=future[0]
            if (is_membership(condition.test,queue) and isinstance(condition.test.ops[0],ast.NotIn)
                    and not condition.orelse and len(condition.body)==1 and is_append(condition.body[0])):
                return dict(kind="append_if_not_member",line=condition.body[0].lineno,guard=ast.unparse(condition.test),
                            same_instance_on_append=True,same_instance_if_append_skipped=None,
                            skipped_branch="membership equality does not prove object identity",normal_completion_only=True)
        return dict(kind="unknown",line=None,same_instance_on_append=None,same_instance_if_append_skipped=None)

    writes=[]; appends=[]; regions=[]; calls=[]
    def walk(block,guards,continuation):
        for index,node in enumerate(block):
            future=block[index+1:]+continuation
            if isinstance(node,ast.If):
                guard=dict(line=node.lineno,expression=ast.unparse(node.test),possible_values=sorted(truths(node.test)),taken_branch=True)
                field=attr(node.test,payload)
                if field in bool_fields:
                    regions.append(dict(line=node.lineno,end=node.end_lineno,guard_field=field,guard=guard,
                                        body_may_reach=reachable(guards+[guard]),context_origin="explicit_contract" if field in domains else "unknown_caller_field",
                                        default_used_to_narrow=False))
                walk(node.body,guards+[guard],future)
                walk(node.orelse,guards+[dict(guard,taken_branch=False)],future)
            elif isinstance(node,(ast.Assign,ast.AnnAssign,ast.AugAssign)):
                target=node.targets[0] if isinstance(node,ast.Assign) and len(node.targets)==1 else node.target if isinstance(node,(ast.AnnAssign,ast.AugAssign)) else None
                if target is None or not isinstance(target,(ast.Name,ast.Attribute)):
                    raise Unsupported("unsupported_write_target")
                field=attr(target,obj)
                if isinstance(target,ast.Attribute) and field is None:
                    raise Unsupported("unmodeled_attribute_write")
                if field is not None:
                    if node.value is None:
                        raise Unsupported("annotation_without_value")
                    writes.append(dict(id="write:"+str(node.lineno),line=node.lineno,object_parameter=obj,field=field,
                                       operation=type(node.op).__name__ if isinstance(node,ast.AugAssign) else "Assign",
                                       value=ast.unparse(node.value),guards=guards,may_reach=reachable(guards),enqueue=enqueue(future)))
            elif is_append(node):
                appends.append(dict(line=node.lineno,object_parameter=obj,queue_parameter=queue,guards=guards,may_reach=reachable(guards)))
            if not isinstance(node,ast.If):
                for call in [n for n in ast.walk(node) if isinstance(n,ast.Call)]:
                    if not (attr(call.func,queue)=="append"):
                        calls.append(dict(line=call.lineno,callee=ast.unparse(call.func),status="unknown_effects"))
    walk(helper.body,[],[])
    try:
        link=_caller_link(tree,binding,formals)
    except Unsupported as error:
        link=dict(status="unknown",reason=str(error),projection_fields=None)
    for write in writes:
        write["projected_by_caller"]=write["field"] in link["projection_fields"] if link["status"]=="source_forwarding_identified" else None
        if not write["may_reach"]:
            write["may_flow_to_bulk_call"]=False
        elif link["status"]!="source_forwarding_identified":
            write["may_flow_to_bulk_call"]=None
        elif not write["projected_by_caller"]:
            write["may_flow_to_bulk_call"]=False
        else:
            write["may_flow_to_bulk_call"]=True if write["enqueue"]["kind"]!="unknown" else None
        write["committed_effect_proven"]=False
    return dict(status="local_write_facts_under_declared_scope",binding=binding,guard_regions=regions,writes=writes,
                append_sites=appends,external_calls=calls,caller_link=link,
                interpretation="Guarded field mutation and potential write-list/projection flow; no persistence or identity-equivalence guarantee")


def _caller_link(tree,binding,formals):
    caller=definition(tree.body,ast.FunctionDef,binding["caller"])
    wrapper=definition(tree.body,ast.FunctionDef,binding["bulk_wrapper"])
    if caller.decorator_list:
        raise Unsupported("decorated_caller")
    if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)) for statement in caller.body for n in ast.walk(statement)):
        raise Unsupported("nested_caller_scope")
    if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)) for statement in wrapper.body for n in ast.walk(statement)):
        raise Unsupported("nested_wrapper_scope")
    if any(isinstance(n,(ast.Import,ast.ImportFrom)) for function in [caller,wrapper] for statement in function.body for n in ast.walk(statement)):
        raise Unsupported("local_function_import")
    calls=[n for n in ast.walk(caller) if isinstance(n,ast.Call)]
    helper_calls=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id==binding["helper"]]
    bulk_calls=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id==binding["bulk_wrapper"]]
    if len(helper_calls)!=1 or len(bulk_calls)!=1:
        raise Unsupported("unique_helper_and_bulk_calls_required")
    call,bulk=helper_calls[0],bulk_calls[0]
    if call.keywords or len(call.args)!=len(formals) or any(not isinstance(a,ast.Name) for a in call.args):
        raise Unsupported("simple_positional_helper_binding_required")
    mapping=dict(zip(formals,[a.id for a in call.args]))
    actual=mapping[binding["queue_parameter"]]
    if len(set(mapping.values()))!=len(mapping):
        raise Unsupported("caller_roles_alias")
    if bulk.lineno<=call.lineno or bulk.keywords or len(bulk.args)!=2 or not isinstance(bulk.args[0],ast.Name) or bulk.args[0].id!=actual:
        raise Unsupported("queue_not_forwarded_to_later_bulk_call")
    fields=bulk.args[1]
    if not isinstance(fields,(ast.List,ast.Tuple)) or any(not isinstance(v,ast.Constant) or type(v.value) is not str for v in fields.elts):
        raise Unsupported("literal_bulk_projection_required")
    parents={child:parent for parent in ast.walk(caller) for child in ast.iter_child_nodes(parent)}
    assignments=[n for n in ast.walk(caller) if isinstance(n,ast.Name) and n.id==actual and isinstance(n.ctx,(ast.Store,ast.Del)) and n.lineno<=bulk.lineno]
    if len(assignments)!=1:
        raise Unsupported("caller_queue_rebound")
    initialization=parents[assignments[0]]
    if not isinstance(initialization,(ast.Assign,ast.AnnAssign)) or not isinstance(initialization.value,ast.List) or initialization.value.elts or initialization.lineno>=call.lineno:
        raise Unsupported("fresh_local_queue_required")
    if isinstance(initialization,ast.Assign) and (len(initialization.targets)!=1 or initialization.targets[0] is not assignments[0]):
        raise Unsupported("queue_initializer_alias")
    for node in ast.walk(caller):
        if isinstance(node,ast.Name) and node.id in {binding["helper"],binding["bulk_wrapper"]} and isinstance(node.ctx,(ast.Store,ast.Del)):
            raise Unsupported("local_function_shadowing")
        if isinstance(node,ast.Name) and node.id==actual and isinstance(node.ctx,ast.Load) and initialization.lineno<=node.lineno<=bulk.lineno:
            parent=parents.get(node)
            if not ((parent is call and node in call.args) or (parent is bulk and node is bulk.args[0]) or (isinstance(parent,ast.If) and parent.test is node)):
                raise Unsupported("caller_queue_escape_or_mutation")
    wrapper_args=arguments(wrapper)
    if len(wrapper_args)!=2:
        raise Unsupported("two_wrapper_parameters_required")
    sinks=[n for n in ast.walk(wrapper) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="bulk_update"]
    if len(sinks)!=1 or sinks[0].keywords or len(sinks[0].args)!=2 or [a.id if isinstance(a,ast.Name) else None for a in sinks[0].args]!=wrapper_args:
        raise Unsupported("wrapper_does_not_forward_inputs")
    sink=sinks[0]; wrapper_parents={child:parent for parent in ast.walk(wrapper) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(wrapper):
        if isinstance(node,ast.Name) and node.id in wrapper_args:
            if isinstance(node.ctx,(ast.Store,ast.Del)):
                raise Unsupported("wrapper_input_rebound")
            if isinstance(node.ctx,ast.Load):
                parent=wrapper_parents.get(node)
                if not ((parent is sink and node in sink.args) or (node.id==wrapper_args[0] and isinstance(parent,ast.comprehension) and parent.iter is node)):
                    raise Unsupported("wrapper_input_escape_or_mutation")
    return dict(status="source_forwarding_identified",caller=caller.name,helper_call_line=call.lineno,parameter_mapping=mapping,
                queue_local=actual,queue_initialization_line=initialization.lineno,bulk_call_line=bulk.lineno,
                projection_fields=[v.value for v in fields.elts],wrapper=wrapper.name,wrapper_sink_line=sink.lineno,
                sink=ast.unparse(sink.func),database_effect_summary_complete=False,control_feasibility="unknown",
                interpretation="Same source local list and literal field projection forwarded to an ORM call; earlier deletes, locks, failures and commit effects unresolved")
