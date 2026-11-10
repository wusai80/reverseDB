"""Source-derived region admission with conservative exceptional-flow boundaries.

The existing lazy protocol can be extracted completely. An enclosing model is
NOT admitted merely because its lazy slots can be recognized. Exception and
receiver dependencies are recorded; unresolved regions retain the original entry.
"""
import ast
import hashlib
import inspect
import textwrap

from .checked_ir import canonical
from .lowering import Unsupported
from .ownership import constructor_binding


def resolve(cls,name,after=None):
    mro=cls.__mro__;start=0 if after is None else mro.index(after)+1
    for owner in mro[start:]:
        if name in owner.__dict__:return owner,owner.__dict__[name]
    return None,None


def function(descriptor):
    if isinstance(descriptor,(classmethod,staticmethod)):return descriptor.__func__
    if isinstance(descriptor,property):return descriptor.fget
    return descriptor if inspect.isfunction(descriptor) else None


def parse(fn):
    text=textwrap.dedent(inspect.getsource(fn));tree=ast.parse(text).body[0]
    if not isinstance(tree,(ast.FunctionDef,ast.AsyncFunctionDef)):raise Unsupported('Not a source function')
    return tree,dict(file=inspect.getsourcefile(fn),line=inspect.getsourcelines(fn)[1],sha256=hashlib.sha256(text.encode()).hexdigest())


def graph(cls,roots):
    nodes=[];edges=[];boundaries=[];visited=set();pending=[]
    for name in roots:
        owner,descriptor=resolve(cls,name)
        if owner:pending.append((owner,name,descriptor))
    while pending:
        owner,name,descriptor=pending.pop(0);fn=function(descriptor)
        if fn is None:continue
        ident=owner.__module__+'.'+owner.__qualname__+'.'+name
        if ident in visited:continue
        visited.add(ident)
        if len(visited)>64:
            boundaries.append(dict(kind='analysis_budget',site=ident));break
        try:tree,witness=parse(fn)
        except (OSError,TypeError,Unsupported) as e:
            boundaries.append(dict(kind='unavailable_source',site=ident,reason=str(e)));continue
        args=tree.args.posonlyargs+tree.args.args;receiver=args[0].arg if args else None
        body=list(tree.body)
        if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
        nodes.append(dict(id=ident,source=witness,statements=[dict(line=witness['line']+n.lineno-1,kind=type(n).__name__,source=ast.unparse(n)) for n in body]))
        def link(target_name,kind,node,after=None):
            target_owner,target=resolve(cls,target_name,after)
            if target_owner is None:return
            target_id=target_owner.__module__+'.'+target_owner.__qualname__+'.'+target_name
            edges.append(dict(source=ident,target=target_id,kind=kind,line=witness['line']+node.lineno-1))
            pending.append((target_owner,target_name,target))
        for n in ast.walk(tree):
            if isinstance(n,ast.ExceptHandler):
                catches=ast.unparse(n.type) if n.type else '*'
                boundaries.append(dict(kind='exception_continuation',site=ident,line=witness['line']+n.lineno-1,
                    catches=catches,continuation=[ast.unparse(x) for x in n.body],
                    obligation='Preserve this continuation or establish disjoint may-raise effects; unknown effects cannot discharge it'))
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):
                receiver_node=n.func.value
                if isinstance(receiver_node,ast.Name) and receiver_node.id==receiver:
                    link(n.func.attr,'receiver_dispatch',n)
                elif isinstance(receiver_node,ast.Call) and isinstance(receiver_node.func,ast.Name) and receiver_node.func.id=='super':
                    if receiver_node.args or receiver_node.keywords:
                        boundaries.append(dict(kind='unsupported_super',site=ident,line=witness['line']+n.lineno-1))
                    else:link(n.func.attr,'resolved_super',n,owner)
                elif isinstance(receiver_node,ast.Attribute) and isinstance(receiver_node.value,ast.Name) and receiver_node.value.id==receiver:
                    boundaries.append(dict(kind='resource_call',site=ident,line=witness['line']+n.lineno-1,source=ast.unparse(n)))
            if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name) and n.value.id==receiver:
                link('__getitem__','receiver_subscript',n)
            if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id==receiver and isinstance(n.ctx,ast.Load):
                _,target=resolve(cls,n.attr)
                if isinstance(target,property):link(n.attr,'property_dependency',n)
    return dict(nodes=nodes,edges=edges,boundaries=boundaries,
                scope='Resolved receiver/super/property dependencies; external calls and dynamically selected callbacks are not a complete Python call graph')


def constructor_steps(cls):
    owner,descriptor=resolve(cls,'__init__');fn=function(descriptor)
    if fn is None:return []
    node,witness=parse(fn);steps=[]
    for stmt in node.body:
        if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Constant) and isinstance(stmt.value.value,str):continue
        steps.append(dict(source=ast.unparse(stmt),kind=type(stmt).__name__,line=witness['line']+stmt.lineno-1,owner=owner.__qualname__))
    return steps


def extract(cls,demands,*,access='attribute'):
    if access not in {'attribute','item'} or any(type(k) is not str for k in demands):raise Unsupported('Need explicit access mode and exact demand keys')
    roots=['__init__','__getitem__']+(['__getattr__'] if access=='attribute' else [])
    recovered=graph(cls,roots)
    decision=dict(schema='source-region-boundary/v1',receiver=cls.__module__+'.'+cls.__qualname__,demands=list(demands),access=access,
                  constructor_steps=constructor_steps(cls),source_graph=recovered,admitted=False,program=None,selected='original',
                  unknown_exception_effects='top; no purity/no-throw inference from successful traces')
    try:
        lookup,witness=constructor_binding(cls)
    except (Unsupported,OSError,TypeError,AttributeError,StopIteration) as error:
        decision['reason']='Enclosing constructor is outside the completely checked lazy protocol: '+str(error)
        decision['blocking_obligations']=recovered['boundaries'] or [dict(kind='unsupported_constructor_or_dispatch')]
        return decision
    if access!='item':
        decision['reason']='Attribute-to-item dispatch has not been lowered; keep original'
        decision['blocking_obligations']=recovered['boundaries'] or [dict(kind='attribute_dispatch')]
        return decision
    # Complete source-derived protocol recognized by existing constructor/lookup
    # checks. Operation roles come from that protocol, never a project name.
    ops=[dict(id='allocation',op='lazy_new',module=cls.__module__,args=[],out='h',binding='lazy',raw='raw',converter='convert')]
    for i,key in enumerate(demands):
        ops.append(dict(id='demand'+str(i),op='lazy_get',module=cls.__module__,args=['h'],out='v'+str(i),key=key))
    decision.update(admitted=True,selected='checked-lazy-region',reason='Complete lazy construction and lookup protocol recovered',
                    binding=witness,program=dict(inputs={},ops=ops,outputs=['v'+str(i) for i in range(len(demands))]),blocking_obligations=[])
    return decision


def select_entry(cls,demands,decision,original,candidate_factory,*,access='attribute'):
    """Choose before application effects; never retry original after a candidate."""
    current=extract(cls,demands,access=access)
    if canonical(current)!=canonical(decision):raise Unsupported('Source-derived decision is stale or altered')
    if not current['admitted']:return original
    return candidate_factory(current['program'])
