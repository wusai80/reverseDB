"""Stage source checking/code generation once per private region binding.

The original continuation semantics are reused unchanged. Plans and loaded
source are private and fixed for their lifetime; concurrent monkeypatching is
outside the deployment contract. A changed lazy lookup is rejected before any
row initialization or converter call. Rebinding requires explicit preparation.
Only code is shared: complete raw maps, caches and receiver state remain per row.
"""
import builtins
from types import MappingProxyType

from . import continuation
from .continuation_region import bind_region as original_bind, Region as OriginalRegion
from .lowering import Unsupported, prepare


class Binding(tuple):
    def __new__(cls, original):
        result = super().__new__(cls, original)
        loaders = {}
        guards = []
        for slot, bound in original[1]['lazy'].items():
            binding = bound['binding']
            loaders[slot] = continuation.compile_demand(binding)
            functions = tuple((fn, fn.__code__, fn.__defaults__, fn.__kwdefaults__)
                              for fn in (binding.getitem, binding.missing))
            guards.append((binding, functions))
        result.loaders = MappingProxyType(loaders)
        result.guards = tuple(guards)
        return result

    def validate_live(self):
        for binding, functions in self.guards:
            binding.validate_live()
            for fn, code, defaults, kwdefaults in functions:
                if fn.__code__ is not code or fn.__defaults__ is not defaults or fn.__kwdefaults__ is not kwdefaults:
                    raise Unsupported('Lookup implementation changed; prepare a new region binding')
            for name in ('hasattr', 'KeyError'):
                if binding.getitem.__globals__.get(name, getattr(builtins, name)) is not getattr(builtins, name):
                    raise Unsupported('Lookup builtin changed; prepare a new region binding')


def bind_region(cls):
    return Binding(original_bind(cls))


class Receiver(continuation.VirtualReceiver):
    def __init__(self, binding, resource, raws):
        cls, description = binding[:2]
        self.cls = cls
        self.description = description
        self.state = {description['fields']['resource']: resource,
                      description['fields']['dirty']: set()}
        self.lazy = {}
        self.loaders = binding.loaders
        self.receiver = None
        self.materializations = 0
        for slot, bound in description['lazy'].items():
            self.lazy[slot] = prepare(raws[bound['parameter']] or {}, getattr(cls, bound['convert']))


class Region(OriginalRegion):
    def __init__(self, binding, resource, raws):
        if type(binding) is not Binding:
            raise Unsupported('A prepared, private source binding is required')
        binding.validate_live()
        _, _, self.access, self.item_resume, self.attribute_resume, self.prefix, self.clear, self.record = binding
        self.virtual = Receiver(binding, resource, raws)
        self.registry_calls = 0
