"""Resume source exception handlers with deferred receiver materialization.

This is an experimental composition component, not new admission to the frozen
IR. Protected-body refinement and complete private state remain explicit duties.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
import textwrap

from .lowering import Unsupported,compile_demand,prepare
from .ownership import constructor_binding
from .source_region import resolve,function


def method(cls,name):
    owner,descriptor=resolve(cls,name);fn=function(descriptor)
    if fn is None or not inspect.isfunction(descriptor):raise Unsupported('Need ordinary source method')
    text=textwrap.dedent(inspect.getsource(fn));node=ast.parse(text).body[0]
    body=node.body
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
    return owner,fn,node,body,dict(method=owner.__qualname__+'.'+name,file=inspect.getsourcefile(fn),line=inspect.getsourcelines(fn)[1],sha256=hashlib.sha256(text.encode()).hexdigest())


def lift_handler(cls,name):
    owner,fn,node,body,witness=method(cls,name);args=node.args
    if node.decorator_list or len(args.args)!=2 or args.posonlyargs or args.kwonlyargs or args.defaults or args.vararg or args.kwarg:
        raise Unsupported('Unsupported continuation signature')
    receiver,key=[x.arg for x in args.args]
    if not body or not isinstance(body[-1],ast.Try):raise Unsupported('No terminal exception boundary')
    boundary=body[-1]
    if boundary.orelse or boundary.finalbody or len(boundary.body)!=1 or not isinstance(boundary.body[0],ast.Return):raise Unsupported('Only direct-return protected body is supported')
    if any(h.name==receiver for h in boundary.handlers):raise Unsupported('Handler rebinds receiver')
    protected=boundary.body[0].value
    subscript=isinstance(protected,ast.Subscript) and isinstance(protected.value,ast.Name) and protected.value.id==receiver and isinstance(protected.slice,ast.Name) and protected.slice.id==key
    super_call=(isinstance(protected,ast.Call) and not protected.keywords and len(protected.args)==1 and isinstance(protected.args[0],ast.Name) and protected.args[0].id==key
                and isinstance(protected.func,ast.Attribute) and isinstance(protected.func.value,ast.Call) and isinstance(protected.func.value.func,ast.Name)
                and protected.func.value.func.id=='super' and not protected.func.value.args and not protected.func.value.keywords)
    if not (subscript or super_call):raise Unsupported('Protected body is not one delegated lookup')
    if super_call:
        if protected.func.attr!=name:raise Unsupported('Protected delegation changes method')
        if fn.__globals__.get('super',super) is not super or inspect.getclosurevars(fn).nonlocals.get('__class__') is not owner:
            raise Unsupported('Unknown super resolution')
    for n in ast.walk(ast.Module(body=body[:-1],type_ignores=[])):
        if isinstance(n,ast.Name) and n.id==receiver:raise Unsupported('Receiver effects before protected lookup')
    handler_tree=ast.Module(body=[x for h in boundary.handlers for x in h.body],type_ignores=[])
    for n in ast.walk(handler_tree):
        if isinstance(n,(ast.FunctionDef,ast.Lambda,ast.ClassDef,ast.Yield,ast.Await,ast.Global,ast.Nonlocal)):raise Unsupported('Unsupported handler scope/control')
        if isinstance(n,ast.Name) and n.id in {'super','__class__'}:raise Unsupported('Handler needs a class closure')
        if isinstance(n,ast.Attribute) and n.attr.startswith('__') and not n.attr.endswith('__'):raise Unsupported('Private name mangling in handler is unsupported')
    if set(inspect.getclosurevars(fn).nonlocals)-{'__class__'}:raise Unsupported('Additional closure state')
    used={n.id for n in ast.walk(node) if isinstance(n,ast.Name)}
    attempt='_rd_attempt';materialize='_rd_materialize'
    if attempt in used or materialize in used or key in {attempt,materialize}:raise Unsupported('Generated identifier collision')
    lowered=deepcopy(body);lowered[-1].body[0].value=ast.Call(func=ast.Name(id=attempt,ctx=ast.Load()),args=[],keywords=[])
    class LowerReceiver(ast.NodeTransformer):
        def visit_Name(self,n):
            if n.id==receiver:
                if not isinstance(n.ctx,ast.Load):raise Unsupported('Receiver rebinding in handler')
                return ast.copy_location(ast.Call(func=ast.Name(id=materialize,ctx=ast.Load()),args=[],keywords=[]),n)
            return n
    for h in lowered[-1].handlers:h.body=[LowerReceiver().visit(x) for x in h.body]
    generated=ast.FunctionDef(name='resume',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=x) for x in [attempt,materialize,key]],kwonlyargs=[],kw_defaults=[],defaults=[]),body=lowered,decorator_list=[])
    code=ast.unparse(ast.fix_missing_locations(ast.Module(body=[generated],type_ignores=[])))+'\n'
    namespace=dict(fn.__globals__);exec(compile(code,'<source-exception-continuation>','exec'),namespace)
    record=dict(source=witness,protected_source=ast.unparse(protected),code=code,catches=[ast.unparse(h.type) if h.type else '*' for h in boundary.handlers],
                semantics='Execute protected thunk once; preserve source handlers and active exception; materialize only on receiver demand')
    return namespace['resume'],record


def layout(cls):
    """Recover the existing six-step constructor layout, without field names."""
    owner,fn,node,body,witness=method(cls,'__init__')
    if len(body)!=6 or node.args.kwarg is None or len(node.args.args)!=4 or node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg:raise Unsupported('Unsupported enclosing constructor layout')
    receiver=node.args.args[0].arg;fields={};lazy={};calls=[]
    for i,stmt in enumerate(body):
        if isinstance(stmt,ast.AnnAssign):target,value=stmt.target,stmt.value
        elif isinstance(stmt,ast.Assign) and len(stmt.targets)==1:target,value=stmt.targets[0],stmt.value
        elif isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Call):
            call=stmt.value
            if not isinstance(call.func,ast.Attribute) or not isinstance(call.func.value,ast.Name) or call.func.value.id!=receiver or call.keywords:raise Unsupported('Unknown constructor call')
            calls.append((call.func.attr,call.args));continue
        else:raise Unsupported('Additional constructor operation')
        if not isinstance(target,ast.Attribute) or not isinstance(target.value,ast.Name) or target.value.id!=receiver or not target.attr.startswith('_'):raise Unsupported('Nonprivate initialization assignment')
        if i==0 and isinstance(value,ast.Name) and value.id==node.args.args[1].arg:fields['resource']=target.attr
        elif i==1 and isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id=='set' and not value.args and not value.keywords and fn.__globals__.get('set',set) is set:fields['dirty']=target.attr
        elif i in [2,3] and isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and len(value.args)==2 and not value.keywords:
            raw,convert=value.args
            if not (isinstance(raw,ast.BoolOp) and isinstance(raw.op,ast.Or) and len(raw.values)==2 and isinstance(raw.values[0],ast.Name) and isinstance(raw.values[1],ast.Dict) and not raw.values[1].keys
                    and isinstance(convert,ast.Attribute) and isinstance(convert.value,ast.Name) and convert.value.id==receiver):raise Unsupported('Unknown lazy binding expression')
            if raw.values[0].id!=node.args.args[i].arg:raise Unsupported('Raw mapping parameter order differs from region interface')
            factory=fn.__globals__.get(value.func.id);binding,proof=constructor_binding(factory)
            lazy[target.attr]=dict(factory=factory,binding=binding,proof=proof,parameter=raw.values[0].id,convert=convert.attr)
        else:raise Unsupported('Unsupported initialization effect')
    if len(fields)!=2 or len(lazy)!=2 or len(calls)!=2 or len(calls[0][1])!=1 or not isinstance(calls[0][1][0],ast.Name) or calls[0][1][0].id!=node.args.kwarg.arg or calls[1][1]:raise Unsupported('Unknown initialization call order')
    if cls.__new__ is not object.__new__ or cls.__getattribute__ is not object.__getattribute__ or type(cls).__call__ is not type.__call__ or any('__del__' in c.__dict__ for c in cls.__mro__):raise Unsupported('Unsupported receiver allocation/observation')
    for slot in list(fields.values())+list(lazy):
        descriptor=inspect.getattr_static(cls,slot,None)
        if hasattr(type(descriptor),'__set__'):raise Unsupported('Descriptor-backed initialization slot')
    for bound in lazy.values():
        if type(inspect.getattr_static(cls,bound['convert'])) not in {classmethod,staticmethod}:raise Unsupported('Converter requires a receiver')
    if cls.__setattr__ is not object.__setattr__:
        so,sf,sn,sbody,sw=method(cls,'__setattr__')
        if len(sn.args.args)!=3:raise Unsupported('Unknown attribute-write signature')
        names={p.arg:n for p,n in zip(sn.args.args,['s','k','v'])}
        class Normalize(ast.NodeTransformer):
            def visit_Name(self,n):n.id=names.get(n.id,n.id);return n
        actual=ast.dump(Normalize().visit(ast.Module(body=deepcopy(sbody),type_ignores=[])),include_attributes=False)
        expected=ast.dump(ast.parse('if k.startswith("_"):\n    super().__setattr__(k, v)\nelse:\n    s[k] = v\n'),include_attributes=False)
        next_owner,next_setter=resolve(cls,'__setattr__',so)
        if actual!=expected or next_setter is not object.__setattr__ or sf.__globals__.get('super',super) is not super or inspect.getclosurevars(sf).nonlocals.get('__class__') is not so:
            raise Unsupported('Private assignment has additional effects')
    # The two source-proven lazy bindings keep their original constructor order.
    return dict(fields=fields,lazy=lazy,prefix_method=calls[0][0],state_method=calls[1][0],source=witness)


class VirtualReceiver:
    """Experimental state relation and a one-way transition to the real receiver."""
    def __init__(self,cls,resource,raws,description):
        self.cls=cls;self.description=description
        self.state={description['fields']['resource']:resource,description['fields']['dirty']:set()}
        self.lazy={};self.loaders={};self.receiver=None;self.materializations=0
        for slot,bound in description['lazy'].items():
            raw=raws[bound['parameter']]
            self.lazy[slot]=prepare(raw or {},getattr(cls,bound['convert']))
            self.loaders[slot]=compile_demand(bound['binding'])

    def demand(self,slot,key):return self.loaders[slot](*self.lazy[slot],key)

    def materialize(self):
        if self.receiver is None:
            obj=object.__new__(self.cls)
            # Reuse the exact state map; later initialization writes reach obj.
            object.__setattr__(obj,'__dict__',self.state)
            for slot,bound in self.description['lazy'].items():
                raw,cache,convert=self.lazy[slot]
                wrapper=bound['factory'](raw,convert)
                object.__setattr__(wrapper,bound['binding'].cache,cache)
                self.state[slot]=wrapper
            self.receiver=obj;self.materializations+=1
        return self.receiver
