"""Bounded source-bound continuation experiment for a private model region.

There is no restart fallback. At the first receiver-dependent demand, exact
state is materialized and later demands use the original receiver normally.
"""
import ast
from copy import deepcopy
import inspect
import textwrap

from scripts.initialization_effects import recover_empty_mapping_prefix,recover_private_state_step
from .continuation import VirtualReceiver,layout,lift_handler,method
from .source_region import resolve
from .lowering import Unsupported
from .checked_ir import canonical


def access_binding(cls,description):
    owner,fn,node,body,witness=method(cls,'__getitem__')
    base_owner,base=resolve(cls,'__getitem__',owner)
    # Resolve the super delegate, then recover its direct native-access method.
    _,_,bn,bb,bw=method(base_owner,'__getitem__')
    if len(bb)!=1 or not isinstance(bb[0],ast.Return) or not isinstance(bb[0].value,ast.Call):raise Unsupported('Unknown base item delegate')
    call=bb[0].value;self_name,key=[a.arg for a in bn.args.args]
    if not (isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id==self_name and len(call.args)==1 and isinstance(call.args[0],ast.Name) and call.args[0].id==key
            and all(k.arg is not None and isinstance(k.value,ast.Constant) for k in call.keywords)):raise Unsupported('Unknown native lookup delegation')
    native_name=call.func.attr
    _,native,nn,nb,nw=method(cls,native_name)
    if len(nb)<3 or len(nn.args.args)<2 or nn.args.posonlyargs or nn.args.kwonlyargs or nn.args.vararg or nn.args.kwarg:raise Unsupported('Unsupported native lookup body')
    s,k=[x.arg for x in nn.args.args[:2]]
    try:
        registry_var=nb[0].targets[0].id;registry_method=nb[0].value.func.attr
        field_table=nb[2].test.comparators[0].attr
        fixed_slot=nb[2].body[0].test.comparators[0].attr
        type_method=nb[2].body[1].value.value.func.attr
    except (AttributeError,IndexError):raise Unsupported('No supported native-field prefix')
    class Normalize(ast.NodeTransformer):
        def visit_Name(self,n):n.id={s:'s',k:'k',registry_var:'g'}.get(n.id,n.id);return n
        def visit_Attribute(self,n):
            self.generic_visit(n)
            if isinstance(n.value,ast.Name) and n.value.id=='s':n.attr={registry_method:'registry',field_table:'fields',fixed_slot:'fixed',type_method:'type_of'}.get(n.attr,n.attr)
            return n
    actual=ast.dump(Normalize().visit(ast.Module(body=deepcopy(nb[:3]),type_ignores=[])),include_attributes=False)
    expected=ast.dump(ast.parse('g = s.registry()\nif k in g:\n    return g[k](s)\nif k in s.fields:\n    if k in s.fixed:\n        return s.fixed[k]\n    return s.type_of(k).null\n'),include_attributes=False)
    if actual!=expected or fixed_slot not in description['lazy']:raise Unsupported('Extra effects in native-field prefix')
    if type(inspect.getattr_static(cls,registry_method)) not in {classmethod,staticmethod}:raise Unsupported('Registry lookup requires a receiver')
    fields=inspect.getattr_static(cls,field_table)
    descriptor_source=None
    if type(fields) is not dict:
        get=type(fields).__dict__.get('__get__')
        if not inspect.isfunction(get):raise Unsupported('Unknown field metadata descriptor')
        dt=ast.parse(textwrap.dedent(inspect.getsource(get))).body[0]
        if len(dt.args.args)!=3:raise Unsupported('Unknown descriptor signature')
        instance_name=dt.args.args[1].arg
        if any(isinstance(n,ast.Name) and n.id==instance_name for n in ast.walk(dt)):raise Unsupported('Metadata descriptor requires receiver identity')
        descriptor_source=ast.unparse(dt)
    # Preserve the remainder verbatim, supplying the ALREADY evaluated registry.
    tail_node=deepcopy(nn);tail_node.name='resume_native'
    reserved='_rd_registry';field_thunk='_rd_fields'
    if any(isinstance(n,ast.Name) and n.id in {reserved,field_thunk} for n in ast.walk(tail_node)):raise Unsupported('Generated identifier collision')
    tail_node.decorator_list=[];tail_node.returns=None
    for a in tail_node.args.args:a.annotation=None
    tail_node.args.args.insert(2,ast.arg(arg=reserved))
    tail_node.args.args.insert(3,ast.arg(arg=field_thunk))
    tail_node.body=deepcopy(nb);tail_node.body[0].value=ast.Name(id=reserved,ctx=ast.Load())
    tail_node.body[2].test.comparators[0]=ast.Call(func=ast.Name(id=field_thunk,ctx=ast.Load()),args=[],keywords=[])
    code=ast.unparse(ast.fix_missing_locations(ast.Module(body=[tail_node],type_ignores=[])))+'\n'
    ns=dict(native.__globals__);exec(compile(code,'<native-lookup-continuation>','exec'),ns)
    return dict(registry=registry_method,fields=field_table,fixed_slot=fixed_slot,resume=ns['resume_native'],
                kwargs={x.arg:x.value.value for x in call.keywords},source=[witness,bw,nw],descriptor_source=descriptor_source,code=code)


def bind_region(cls):
    description=layout(cls);access=access_binding(cls,description)
    item_resume,iw=lift_handler(cls,'__getitem__');attribute_resume,aw=lift_handler(cls,'__getattr__')
    prefix_plan=recover_empty_mapping_prefix(cls,description['prefix_method'])
    state_plan=recover_private_state_step(cls,description['state_method'])
    # Constructor/private-state writes must not bypass descriptor effects.
    for slot in state_plan['slots']:
        if hasattr(type(inspect.getattr_static(cls,slot,None)),'__set__'):raise Unsupported('Descriptor-backed private state step')
    ns={};exec(prefix_plan['code'],ns);prefix=ns['run_initialization_prefix']
    ns={};exec(state_plan['code'],ns);clear=ns['run_private_state_step']
    record=dict(schema='source-exception-resumption/v1',constructor=description['source'],
        lazy={k:v['proof'] for k,v in description['lazy'].items()},item_handler=iw,attribute_handler=aw,
        native_lookup=dict(source=access['source'],descriptor_source=access['descriptor_source'],resume_code=access['code']),prefix=prefix_plan,state=state_plan,
        admitted_to_frozen_IR=False,source_graph_complete=False)
    return cls,description,access,item_resume,attribute_resume,prefix,clear,record


def check_record(cls,record):
    """Re-derive source/code binding; this is not the frozen IR proof checker."""
    try:
        actual=bind_region(cls)[-1]
        return dict(accepted=canonical(actual)==canonical(record),scope='source-linked continuation component only')
    except (Unsupported,ValueError,TypeError,AttributeError,OSError) as error:
        return dict(accepted=False,reason=str(error))


class Region:
    def __init__(self,binding,resource,raws):
        cls,description,self.access,self.item_resume,self.attribute_resume,self.prefix,self.clear,self.record=binding
        self.virtual=VirtualReceiver(cls,resource,raws,description)
        self.registry_calls=0

    def native(self,key):
        virtual=self.virtual;cls=virtual.cls;access=self.access
        registry=getattr(cls,access['registry'])();self.registry_calls+=1
        if type(registry) is not dict or key in registry:
            obj=virtual.materialize()
            return access['resume'](obj,key,registry,lambda:getattr(obj,access['fields']),**access['kwargs'])
        fields=getattr(cls,access['fields']);raw,cache,_=virtual.lazy[access['fixed_slot']]
        # Exact containers make these speculative tests unobservable. Any other
        # path resumes the complete original body after the registry evaluation.
        if type(registry) is dict and type(fields) is dict and key not in registry and key in fields and (key in cache or key in raw):
            return virtual.demand(access['fixed_slot'],key)
        obj=virtual.materialize()
        return access['resume'](obj,key,registry,lambda:fields,**access['kwargs'])

    def load(self,key):
        if type(key) is not str:raise Unsupported('Exact attribute key required')
        virtual=self.virtual
        if virtual.receiver is not None:return getattr(virtual.receiver,key)
        # Class attributes/descriptors use the original attribute route directly.
        marker=object()
        if inspect.getattr_static(virtual.cls,key,marker) is not marker:
            return getattr(virtual.materialize(),key)
        return self.attribute_resume(lambda:self.item_resume(lambda:self.native(key),virtual.materialize,key),virtual.materialize,key)

    def initialize(self):
        self.prefix(self.load)
        self.clear(self.virtual.state)

    def run(self,demands):
        self.initialize()
        return tuple(self.load(k) for k in demands)
