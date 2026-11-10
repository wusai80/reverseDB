"""Development IR examples, not independently recovered applications."""


def op(ident, kind, args=(), out=None, module='host', **extra):
    item = dict(id=ident, op=kind, args=list(args), module=module, **extra)
    if out is not None: item['out'] = out
    return item


def programs():
    interface = dict(key='i64', other='i64', value='i64', enabled='bool')
    packet = [
        op('r', 'read', ['key'], 'row', 'storage'),
        op('p', 'pack', ['row', 'value'], 'packet', 'model'),
        op('f', 'field', ['packet'], 'selected', 'consumer', index=0),
    ]
    cases = {
        'packet_projection': (packet, ['selected']),
        'packet_escape': (packet, ['packet', 'selected']),
        'dead_read': ([op('unused', 'read', ['other'], 'discarded')] + packet, ['selected']),
        'guard_event_write': ([
            op('before', 'emit', ['value'], label='attempt'),
            op('w', 'write', ['key', 'value'], module='storage'),
            op('guard', 'check', ['enabled'], code='denied'),
        ] + packet, ['selected']),
        'checked_add': ([
            op('one', 'const', out='one', value=1),
            op('unused_add', 'add', ['value', 'one'], 'sum'),
        ] + packet, ['selected']),
        'read_write_read': ([
            op('first', 'read', ['key'], 'old', 'storage'),
            op('w', 'write', ['key', 'value'], module='storage'),
            op('again', 'read', ['key'], 'new', 'storage'),
            op('pair', 'pack', ['old', 'new'], 'pair', 'model'),
            op('old_field', 'field', ['pair'], 'old_result', 'consumer', index=0),
            op('new_field', 'field', ['pair'], 'new_result', 'consumer', index=1),
        ], ['old_result', 'new_result']),
        'nested_packet': ([
            op('r', 'read', ['key'], 'row', 'storage'),
            op('inner', 'pack', ['row', 'enabled'], 'inner', 'model'),
            op('outer', 'pack', ['inner', 'value'], 'outer', 'model'),
            op('inner_field', 'field', ['outer'], 'inner_again', 'consumer', index=0),
            op('value_field', 'field', ['inner_again'], 'result', 'consumer', index=0),
            op('dead_const', 'const', out='zero', value=0),
            op('dead_eq', 'eq', ['value', 'zero'], 'equal'),
        ], ['result']),
        'effect_after_read': ([
            op('r', 'read', ['key'], 'row', 'storage'),
            op('e', 'emit', ['row'], label='read'),
            op('p', 'pack', ['row'], 'packet', 'model'),
            op('f', 'field', ['packet'], 'result', 'consumer', index=0),
        ], ['result']),
        'boolean_escape': ([
            op('r', 'read', ['key'], 'row', 'storage'),
            op('eq', 'eq', ['row', 'value'], 'same'),
            op('p', 'pack', ['same', 'enabled'], 'packet', 'model'),
            op('f', 'field', ['packet'], 'result', 'consumer', index=0),
        ], ['result', 'packet']),
        'two_writes_then_check': ([
            op('first_write', 'write', ['key', 'value'], module='storage'),
            op('second_write', 'write', ['other', 'value'], module='storage'),
            op('guard', 'check', ['enabled'], code='denied'),
        ], []),
    }
    return {name:dict(inputs=dict(interface), ops=ops, outputs=outputs) for name, (ops, outputs) in cases.items()}
