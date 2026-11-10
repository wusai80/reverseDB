"""Restricted source specialization for an empty-mapping initialization prefix.

This is a syntax-directed compiler experiment, not a deletion classifier. It
does not certify whole constructors or receiver-dependent plugin behavior.
Field and method names come from source/MRO resolution, never application rules.
"""
import ast
import builtins
import copy
import hashlib
import inspect
import textwrap


class UnsupportedPrefix(ValueError):
    pass


def resolve_method(cls, name, after=None):
    mro=cls.__mro__;start=0 if after is None else mro.index(after)+1
    for owner in mro[start:]:
        if name in owner.__dict__:
            fn=owner.__dict__[name]
            if not inspect.isfunction(fn):raise UnsupportedPrefix('Method is not a plain source function')
            return owner,fn
    raise UnsupportedPrefix('Unresolved method')


def source_tree(fn):
    text=textwrap.dedent(inspect.getsource(fn));node=ast.parse(text).body[0]
    if not isinstance(node,ast.FunctionDef) or node.decorator_list or inspect.isgeneratorfunction(fn):
        raise UnsupportedPrefix('Unsupported decorated/generator method')
    body=list(node.body)
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
    return node,body,dict(function=fn.__qualname__,source_file=inspect.getsourcefile(fn),
        source_line=inspect.getsourcelines(fn)[1],function_sha256=hashlib.sha256(text.encode()).hexdigest())


def recover_empty_mapping_prefix(cls, method_name):
    """Keep evaluation of `predicate and literal in empty_mapping` exactly.

    Support one leading super call whose body is solely an empty mapping loop,
    followed by one if with no else. Other forms remain unsupported. A fresh
    exact builtin empty dict is the invocation premise, not sampled data.
    """
    owner,fn=resolve_method(cls,method_name);node,body,witness=source_tree(fn)
    if (len(node.args.args)!=2 or node.args.posonlyargs or node.args.defaults or
            node.args.vararg or node.args.kwarg or node.args.kwonlyargs or len(body)!=2):
        raise UnsupportedPrefix('Unsupported mapping method signature/body')
    self_name,arg=node.args.args[0].arg,node.args.args[1].arg
    if arg=='_load' or fn.__globals__.get('super',builtins.super) is not builtins.super:
        raise UnsupportedPrefix('Generated-name collision or shadowed super')
    closure=inspect.getclosurevars(fn)
    if closure.nonlocals.get('__class__') is not owner:
        raise UnsupportedPrefix('Super lexical owner differs from MRO owner')
    first,last=body
    if not (isinstance(first,ast.Expr) and isinstance(first.value,ast.Call)):
        raise UnsupportedPrefix('Missing leading base mapping call')
    call=first.value
    if not (isinstance(call.func,ast.Attribute) and call.func.attr==method_name and
            isinstance(call.func.value,ast.Call) and isinstance(call.func.value.func,ast.Name) and call.func.value.func.id=='super' and
            not call.func.value.args and not call.func.value.keywords and len(call.args)==1 and
            isinstance(call.args[0],ast.Name) and call.args[0].id==arg and not call.keywords):
        raise UnsupportedPrefix('Base call is not the same empty mapping operation')
    parent,base_fn=resolve_method(cls,method_name,after=owner);base_node,base_body,base_witness=source_tree(base_fn)
    if (len(base_node.args.args)!=2 or base_node.args.posonlyargs or base_node.args.defaults or
            base_node.args.vararg or base_node.args.kwarg or base_node.args.kwonlyargs or
            len(base_body)!=1 or not isinstance(base_body[0],ast.For)):
        raise UnsupportedPrefix('Base operation has additional effects')
    loop=base_body[0];it=loop.iter;base_arg=base_node.args.args[1].arg
    if not (isinstance(it,ast.Call) and isinstance(it.func,ast.Attribute) and it.func.attr=='items' and
            isinstance(it.func.value,ast.Name) and it.func.value.id==base_arg and not it.args and not it.keywords and not loop.orelse):
        raise UnsupportedPrefix('Base loop is not over builtin empty mapping items')
    if not (isinstance(last,ast.If) and not last.orelse and isinstance(last.test,ast.BoolOp) and isinstance(last.test.op,ast.And)):
        raise UnsupportedPrefix('Unsupported control structure')
    tail=last.test.values[-1]
    if not (isinstance(tail,ast.Compare) and len(tail.ops)==1 and isinstance(tail.ops[0],ast.In) and
            isinstance(tail.left,ast.Constant) and type(tail.left.value) in [str,int] and
            len(tail.comparators)==1 and isinstance(tail.comparators[0],ast.Name) and tail.comparators[0].id==arg):
        raise UnsupportedPrefix('No guaranteed-false empty-mapping tail')
    # Do not rewrite comparison/truthiness or prune the earlier operands.
    fields=[]
    class Lower(ast.NodeTransformer):
        def visit_Attribute(self,n):
            if not (isinstance(n.value,ast.Name) and n.value.id==self_name and isinstance(n.ctx,ast.Load)):
                raise UnsupportedPrefix('Receiver-dependent/nested attribute expression')
            fields.append(n.attr)
            return ast.copy_location(ast.Call(func=ast.Name(id='_load',ctx=ast.Load()),args=[ast.Constant(n.attr)],keywords=[]),n)
        def visit_Call(self,n):raise UnsupportedPrefix('Unknown call in initialization condition')
        def visit_Name(self,n):
            if n.id!=arg:raise UnsupportedPrefix('Unknown external name in condition')
            return n
    supported=(ast.BoolOp,ast.And,ast.Or,ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,
               ast.Is,ast.IsNot,ast.In,ast.NotIn,ast.UnaryOp,ast.Not,ast.Constant,ast.Attribute,ast.Name,ast.Load)
    if any(not isinstance(n,supported) for n in ast.walk(last.test)):
        raise UnsupportedPrefix('Unsupported condition expression')
    condition=Lower().visit(copy.deepcopy(last.test))
    generated=ast.FunctionDef(name='run_initialization_prefix',
        args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='_load')],kwonlyargs=[],kw_defaults=[],defaults=[]),
        body=[ast.Assign(targets=[ast.Name(id=arg,ctx=ast.Store())],value=ast.Dict(keys=[],values=[])),
              ast.If(test=condition,body=[ast.Pass()],orelse=[])],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[generated],type_ignores=[]));code=ast.unparse(module)+'\n'
    return dict(kind='source-derived-empty-mapping-prefix/v1',code=code,field_reads=fields,authorizes_lowering=False,
        dispatch=dict(receiver=cls.__qualname__,method=method_name,resolved_owner=owner.__qualname__,base_owner=parent.__qualname__),
        source=[witness,base_witness],premise='Fresh exact builtin empty dict, source-bound normal receiver dispatch; no frame introspection',
        preserves=['left-to-right attribute demand','original comparison and truth testing','exceptions before false tail'],
        not_proved=['whole-constructor purity','arbitrary getters or receiver identity','safe database-read deletion'])


def recover_private_state_step(cls, method_name):
    """Translate receiver-private state access to a local record; retain the body.

    The caller must justify private-state non-escape and provide initial state.
    This preserves resource truthiness/revision access, not their purity.
    """
    owner,fn=resolve_method(cls,method_name);node,body,witness=source_tree(fn)
    if (len(node.args.args)!=1 or node.args.posonlyargs or node.args.defaults or
            node.args.vararg or node.args.kwarg or node.args.kwonlyargs):
        raise UnsupportedPrefix('Unsupported state-step signature')
    if fn.__globals__.get('set',builtins.set) is not builtins.set or 'set' in inspect.getclosurevars(fn).nonlocals:
        raise UnsupportedPrefix('State allocation uses a shadowed builtin')
    receiver=node.args.args[0].arg;slots=[]
    class Lower(ast.NodeTransformer):
        def visit_Attribute(self,n):
            if isinstance(n.value,ast.Name) and n.value.id==receiver:
                if not n.attr.startswith('_'):raise UnsupportedPrefix('Public receiver state needs object semantics')
                slots.append(n.attr)
                return ast.copy_location(ast.Subscript(value=ast.Name(id='_state',ctx=ast.Load()),slice=ast.Constant(n.attr),ctx=n.ctx),n)
            return self.generic_visit(n)
        def visit_Name(self,n):
            if n.id==receiver:raise UnsupportedPrefix('Receiver escapes private-state step')
            if n.id!='set':raise UnsupportedPrefix('Unknown external state-step name')
            return n
    # Deliberately narrow source scope; no arbitrary calls or writes to resources.
    for n in ast.walk(ast.Module(body=body,type_ignores=[])):
        if not isinstance(n,(ast.Module,ast.Assign,ast.If,ast.Attribute,ast.Name,ast.Load,ast.Store,ast.Call)):
            raise UnsupportedPrefix('Unsupported state-step syntax')
        if isinstance(n,ast.Assign) and any(not isinstance(target,ast.Attribute) for target in n.targets):
            raise UnsupportedPrefix('Local rebinding in state step')
        if isinstance(n,ast.Call) and not (isinstance(n.func,ast.Name) and n.func.id=='set' and not n.args and not n.keywords):
            raise UnsupportedPrefix('Unknown call in state step')
        if isinstance(n,ast.Attribute) and isinstance(n.ctx,ast.Store) and not (isinstance(n.value,ast.Name) and n.value.id==receiver):
            raise UnsupportedPrefix('Resource mutation in state step')
        if isinstance(n,(ast.Return,ast.Raise,ast.For,ast.While,ast.Try,ast.With,ast.Lambda,ast.Yield)):
            raise UnsupportedPrefix('Unsupported state-step control/escape')
    lowered=Lower().visit(copy.deepcopy(ast.Module(body=body,type_ignores=[])))
    generated=ast.FunctionDef(name='run_private_state_step',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='_state')],kwonlyargs=[],kw_defaults=[],defaults=[]),
                             body=lowered.body,decorator_list=[])
    code=ast.unparse(ast.fix_missing_locations(ast.Module(body=[generated],type_ignores=[])))+'\n'
    return dict(kind='source-private-state-step/v1',code=code,slots=slots,source=witness,
        dispatch=dict(receiver=cls.__qualname__,method=method_name,resolved_owner=owner.__qualname__),
        premise='Private state does not escape; globals and builtin set follow pinned source environment',authorizes_lowering=False)
