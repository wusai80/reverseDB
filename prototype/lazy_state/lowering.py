"""Source-checked lowering of a narrow lazy-map protocol to explicit state.

No callback purity inference: conversions remain ordered and may fail. This
component is separate from the frozen scalar IR and grants no whole-ORM proof.
"""
import ast
import builtins
from dataclasses import dataclass
import hashlib
import inspect
import json
import textwrap


class Unsupported(ValueError): pass


def tree(fn):
    source=textwrap.dedent(inspect.getsource(fn))
    node=ast.parse(source).body[0]
    if not isinstance(node,ast.FunctionDef) or node.decorator_list:
        raise Unsupported('Need plain source method')
    args=node.args
    if args.posonlyargs or args.kwonlyargs or args.defaults or args.vararg or args.kwarg or len(args.args)!=2:
        raise Unsupported('Unsupported lookup signature')
    if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str): node.body=node.body[1:]
    return node,dict(file=inspect.getsourcefile(fn),line=inspect.getsourcelines(fn)[1],sha256=hashlib.sha256(source.encode()).hexdigest())


def shape(body): return ast.dump(ast.Module(body=body,type_ignores=[]),include_attributes=False)


GET='''if k in s.cache:
    return s.cache[k]
if hasattr(s.__class__, "__missing__"):
    return s.__class__.__missing__(s, k)
raise KeyError(k)
'''
MISS='''if k in s.raw:
    v = s.convert(k, s.raw[k])
    s.cache[k] = v
    return v
return None
'''
CODE='''def demand(raw, cache, convert, key):
    if key in cache:
        return cache[key]
    if key in raw:
        value = convert(key, raw[key])
        cache[key] = value
        return value
    return None
'''


@dataclass(frozen=True)
class Binding:
    cls: type
    getitem: object
    missing: object
    raw: str
    cache: str
    convert: str
    witness: dict

    def validate_live(self):
        if self.cls.__getitem__ is not self.getitem or self.cls.__missing__ is not self.missing:
            raise Unsupported('Lookup dispatch changed since source binding')


def bind(cls):
    for method in ['__getitem__','__missing__']:
        owner=next((c for c in cls.__mro__ if method in c.__dict__),None)
        if owner is None or not inspect.isfunction(owner.__dict__[method]):
            raise Unsupported('Lookup dispatch must be an ordinary instance method')
    if cls.__getattribute__ is not object.__getattribute__ or any('__getattr__' in c.__dict__ for c in cls.__mro__):
        raise Unsupported('Dynamic receiver attribute access')
    get,gw=tree(cls.__getitem__); miss,mw=tree(cls.__missing__)
    gs,gk=[a.arg for a in get.args.args]; ms,mk=[a.arg for a in miss.args.args]
    try:
        raw=miss.body[0].test.comparators[0].attr
        body=miss.body[0].body
        value=body[0].targets[0].id
        convert=body[0].value.func.attr
        cache=body[1].targets[0].value.attr
    except (AttributeError,IndexError): raise Unsupported('No supported lazy-miss transition')
    if len({raw,cache,convert})!=3: raise Unsupported('Aliased storage/converter slots')
    # Slot descriptors could introduce hidden effects not present in map state.
    if any(any(slot in c.__dict__ for c in cls.__mro__) for slot in [raw,cache,convert]):
        raise Unsupported('Storage/converter slots must be plain instance fields')
    class Normalize(ast.NodeTransformer):
        def __init__(self,names,attrs):self.names=names;self.attrs=attrs
        def visit_Name(self,n):n.id=self.names.get(n.id,n.id);return n
        def visit_Attribute(self,n):
            self.generic_visit(n)
            if isinstance(n.value,ast.Name) and n.value.id=='s':n.attr=self.attrs.get(n.attr,n.attr)
            return n
    normalized_get=Normalize({gs:'s',gk:'k'},{cache:'cache'}).visit(get)
    normalized_miss=Normalize({ms:'s',mk:'k',value:'v'},{raw:'raw',cache:'cache',convert:'convert'}).visit(miss)
    if shape(normalized_get.body)!=shape(ast.parse(GET).body) or shape(normalized_miss.body)!=shape(ast.parse(MISS).body):
        raise Unsupported('Lookup includes unsupported branches, effects or cache transitions')
    for name in ['hasattr','KeyError']:
        if cls.__getitem__.__globals__.get(name,getattr(builtins,name)) is not getattr(builtins,name):
            raise Unsupported('Lookup builtin is shadowed')
    witness=dict(schema='lazy-demand-binding/v1',class_name=cls.__qualname__,source=[gw,mw],
        slots=dict(raw=raw,cache=cache,convert=convert),generated_sha256=hashlib.sha256(CODE.encode()).hexdigest(),
        premise='Exact dictionaries and string keys; private wrapper/cache; no receiver/frame/allocator observations; stable dispatch',
        converter_pure=False,whole_constructor_proved=False)
    return Binding(cls,cls.__getitem__,cls.__missing__,raw,cache,convert,witness)


def compile_demand(binding):
    binding.validate_live()
    recovered=bind(binding.cls)
    if (binding.raw,binding.cache,binding.convert)!=(recovered.raw,recovered.cache,recovered.convert) or json.dumps(binding.witness,sort_keys=True)!=json.dumps(recovered.witness,sort_keys=True):
        raise Unsupported('Binding differs from source re-extraction')
    ns={};exec(compile(CODE,'<lazy-demand-lowering>','exec'),ns)
    return ns['demand']


def prepare(raw,convert):
    # Preserve every raw value and the raw dictionary reference. Do not trim
    # fields or eagerly convert values. Callable semantics are intentionally opaque.
    if type(raw) is not dict or any(type(k) is not str for k in raw) or not callable(convert):
        raise Unsupported('Need exact string-keyed raw dict and converter')
    return raw,{},convert
