"""Check a fresh-wrapper constructor before introducing a private IR handle."""
import ast
import hashlib
import inspect
import textwrap

from .lowering import Unsupported,bind,shape


def constructor_binding(cls):
    lookup=bind(cls)
    if cls.__new__ is not object.__new__ or cls.__setattr__ is not object.__setattr__ or type(cls).__call__ is not type.__call__ or any('__del__' in c.__dict__ for c in cls.__mro__):
        raise Unsupported('Observable allocation, attribute writes or finalization')
    owner=next(c for c in cls.__mro__ if '__init__' in c.__dict__)
    fn=owner.__dict__['__init__']
    if not inspect.isfunction(fn):raise Unsupported('Unknown constructor dispatch')
    text=textwrap.dedent(inspect.getsource(fn));node=ast.parse(text).body[0];a=node.args
    if node.decorator_list or len(a.args)!=3 or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults:
        raise Unsupported('Constructor is not the supported fresh-map initializer')
    self_name,raw_name,convert_name=[p.arg for p in a.args]
    if fn.__globals__.get('super',super) is not super or inspect.getclosurevars(fn).nonlocals.get('__class__') is not owner:
        raise Unsupported('Unknown super dispatch')
    class Normalize(ast.NodeTransformer):
        def visit_Name(self,n):n.id={self_name:'s',raw_name:'r',convert_name:'f'}.get(n.id,n.id);return n
        def visit_Attribute(self,n):
            self.generic_visit(n)
            if isinstance(n.value,ast.Name) and n.value.id=='s':n.attr={lookup.raw:'raw',lookup.convert:'convert'}.get(n.attr,n.attr)
            return n
    body=node.body
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
    normalized=Normalize().visit(ast.Module(body=body,type_ignores=[]))
    expected=ast.parse('super().__init__()\ns.raw = r\ns.convert = f\n')
    if shape(normalized.body)!=shape(expected.body):raise Unsupported('Constructor has extra effects or state')
    remaining=cls.__mro__[cls.__mro__.index(owner)+1:]
    parent=next(c for c in remaining if '__init__' in c.__dict__)
    base_fn=parent.__dict__['__init__'];base_text=textwrap.dedent(inspect.getsource(base_fn));base=ast.parse(base_text).body[0]
    # Check the source of the resolved empty base initializer, not a class name.
    a=base.args
    if len(a.posonlyargs)!=2 or a.args or a.vararg or a.kwonlyargs or a.kwarg is None or len(a.defaults)!=1 or not isinstance(a.defaults[0],ast.Constant) or a.defaults[0].value is not None:
        raise Unsupported('Unsupported empty base constructor binding')
    s,d=[p.arg for p in a.posonlyargs];kw=a.kwarg.arg
    class BaseNormalize(ast.NodeTransformer):
        def visit_Name(self,n):n.id={s:'s',d:'d',kw:'kw'}.get(n.id,n.id);return n
        def visit_Attribute(self,n):
            self.generic_visit(n)
            if isinstance(n.value,ast.Name) and n.value.id=='s' and n.attr==lookup.cache:n.attr='cache'
            return n
    norm=BaseNormalize().visit(base)
    expected=ast.parse('s.cache = {}\nif d is not None:\n    s.update(d)\nif kw:\n    s.update(kw)\n')
    if shape(norm.body)!=shape(expected.body):raise Unsupported('Base initialization has additional effects')
    witness=dict(lookup=lookup.witness,constructor=dict(file=inspect.getsourcefile(fn),sha256=hashlib.sha256(text.encode()).hexdigest()),
                 base_constructor=dict(file=inspect.getsourcefile(base_fn),sha256=hashlib.sha256(base_text.encode()).hexdigest()),
                 fresh_cache=True,raw_alias_retained=True,
                 scope='Lazy wrapper construction only; not surrounding ORM construction')
    return lookup,witness
