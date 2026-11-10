"""Development integration adapter, not an automatic whole-Item extractor."""
from .lowering import prepare, Unsupported


def consume(core,model,rows,flex,resource,prefix,clear,demands,lazy_load,*,getters):
    if any(key not in model._fields or key in getters for key in demands):
        raise Unsupported('Consumer demand requires non-native/receiver-dependent semantics')
    packet=core.Results(model,list(rows),resource,list(flex))
    if not rows:return packet[0]  # Keep original empty-result failure.
    indexed=packet._get_indexed_flex_attrs()
    row=rows[0]
    fixed_raw={key:value for key,value in dict(row).items() if key[:4]!='flex'}
    fixed=prepare(fixed_raw,model._convert)
    flexible=prepare(indexed.get(row['id'],{}) or {},model._convert)
    state={'_db':resource,'_dirty':set()}
    def load(key):
        # Original registry lookup remains at every demand, including cache hits.
        active=model._getters()
        if key in active or key not in model._fields:
            raise Unsupported('Demand needs receiver-dependent semantics; no retry/fallback after effects')
        if key in fixed[1] or key in fixed[0]:return lazy_load(*fixed,key)
        return model._type(key).null
    prefix(load)
    clear(state)
    values=tuple(load(key) for key in demands)
    return values,state,fixed,flexible
