"""Reuse the frozen semantic suite, then test the new staging boundary."""
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

from . import continuation, staged_region, verify_continuation as original
from .lowering import Unsupported


def main():
    out = Path(sys.argv[sys.argv.index('--out') + 1])
    compile_original = continuation.compile_demand
    compilations = []
    def compile_once(binding):
        compilations.append(binding.cls.__qualname__)
        return compile_original(binding)
    def bind(cls):
        with patch.object(continuation, 'compile_demand', compile_once):
            return staged_region.bind_region(cls)
    with patch.object(original, 'bind_region', bind), patch.object(original, 'Region', staged_region.Region), \
         patch.object(continuation, 'compile_demand', side_effect=AssertionError('Runtime compilation')):
        original.main()
    from beets.library import Item
    binding = bind(Item)
    parameters = [v['parameter'] for v in binding[1]['lazy'].values()]
    raws = {p: {} for p in parameters}
    checks = []
    def refused(name, function):
        try:
            function()
        except Unsupported:
            checks.append(name)
        else:
            raise AssertionError(name)
    refused('unprepared tuple rejected', lambda: staged_region.Region(tuple(binding), None, raws))
    lazy = next(iter(binding[1]['lazy'].values()))['binding']
    for name in ['__getitem__', '__missing__']:
        with patch.object(lazy.cls, name, lambda s, k: None):
            refused(name + ' replacement rejected', lambda: staged_region.Region(binding, None, raws))
    for fn in [lazy.getitem, lazy.missing]:
        code = fn.__code__
        try:
            fn.__code__ = (lambda self, key: None).__code__
            refused('same function changed code rejected', lambda: staged_region.Region(binding, None, raws))
        finally:
            fn.__code__ = code
    for name in ['hasattr', 'KeyError']:
        with patch.dict(lazy.getitem.__globals__, {name: lambda *a: None}):
            refused('shadowed ' + name + ' rejected', lambda: staged_region.Region(binding, None, raws))
    with patch.object(continuation, 'compile_demand', side_effect=AssertionError('Runtime compilation')):
        a = staged_region.Region(binding, None, raws)
        b = staged_region.Region(binding, None, raws)
    assert a.virtual.loaders is b.virtual.loaders
    assert a.virtual.state is not b.virtual.state
    for slot in a.virtual.lazy:
        assert a.virtual.lazy[slot][1] is not b.virtual.lazy[slot][1]
        assert a.virtual.lazy[slot][0] == b.virtual.lazy[slot][0]
    checks.append('code shared while state and successful caches remain private')
    assert len(compilations) == 4, compilations
    checks.append('exactly two compilation calls per preparation; none per row')
    report = original.json.loads((out / 'summary.json').read_text())
    report.update(implementation='staged_region', staging_checks=checks,
                  compilation_calls=len(compilations), preparations=2,
                  per_row_compilation_calls=0, stable_loaded_source_required=True,
                  concurrent_monkeypatching_supported=False)
    original.save(out / 'summary.json', report)
    for file in [Path(staged_region.__file__), Path(__file__)]:
        dst = out / 'inputs' / file.relative_to(Path.cwd())
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, dst)
    original.save(out / 'manifest.json', {str(p.relative_to(out)): original.sha(p)
                  for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json'})
    print(json.dumps(dict(staging_checks=len(checks), per_row_compilation_calls=0, status='pass')))


if __name__ == '__main__':
    main()
