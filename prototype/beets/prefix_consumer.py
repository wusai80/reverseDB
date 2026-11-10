"""Scoped packet experiment: preserve source-derived effects without an Item.

Not installed in the application and not an admitted backend. The caller binds
the fixed model/Results/LazyDict source, full packets, empty constructor kwargs,
fast query/sort, nonescaping private state and noncomputed fixed-field demands.
Arbitrary receiver identity, descriptors, tracing, allocation failure and
KeyError album-fallback behavior are outside this experiment's contract.
"""
from initialization_effects import UnsupportedPrefix


def consume_packet(core, model, rows, flex, db, prefix, clear_state, demands):
    packet = core.Results(model, list(rows), db, list(flex))
    if not rows:
        return packet[0]  # Retain the source's exact empty-result exception.
    indexed = packet._get_indexed_flex_attrs()
    row = rows[0]
    fixed_values = {key: value for key, value in dict(row).items() if key[:4] != 'flex'}
    state = {'_db': db, '_dirty': set()}
    convert = model._convert
    fixed = core.LazyDict(fixed_values or {}, convert)
    flexible = core.LazyDict(indexed.get(row['id'], {}) or {}, convert)

    def load(key):
        getters = model._getters()  # Retain lookup at each original demand.
        if key in getters or key not in model._fields:
            # Deliberately no fallback after partially executing the prefix.
            raise UnsupportedPrefix('Demand needs receiver-dependent field semantics')
        return fixed[key] if key in fixed else model._type(key).null

    prefix(load)
    clear_state(state)
    values = tuple(load(key) for key in demands)
    # Local diagnostic state only. Production scalar consumers would not expose it.
    return values, state, fixed, flexible
