"""Authored development programs, not held-out applications or decompilations."""

INPUTS = dict(key='i64',other='i64',value='i64',enabled='bool')


def bundles():
    storage = '''def load(rt, key):
    return rt.read("read", key)
'''
    model = '''def build(rt, row, value):
    packet = (row, value)
    return packet
'''
    consumer = '''def take(rt, packet):
    return packet[0]
'''
    source = '''import storage
import model
import consumer

def run(rt, key, other, value, enabled):
    row = storage.load(rt, key)
    packet = model.build(rt, row, value)
    result = consumer.take(rt, packet)
    return result
'''
    common = dict(storage=storage,model=model,consumer=consumer)
    entries = {
        'cross_module': source,
        'guard_and_rollback': source.replace('    row = storage.load', '    rt.emit("attempt", value)\n    rt.write("write", key, value)\n    rt.check(enabled, "denied")\n    row = storage.load'),
        'dead_read': source.replace('    row = storage.load', '    ignored = rt.read("unused", other)\n    row = storage.load'),
        'unused_checked_add': source.replace('    row = storage.load', '    ignored = rt.add(value, 1)\n    row = storage.load'),
        'escaping_packet': source.replace('    return result', '    return (result, packet)'),
        'read_write_read': source.replace('    row = storage.load', '    old = rt.read("before", key)\n    rt.write("write", key, value)\n    row = storage.load').replace('    return result', '    return (old, result)'),
        'nested_and_boolean': source.replace('    return result', '    same = result == value\n    nested = ((result, enabled), same)\n    first = nested[0]\n    return (first[0], nested[1])'),
        'two_writes': source.replace('    row = storage.load', '    rt.write("first", key, value)\n    rt.write("second", other, value)\n    rt.check(enabled, "denied")\n    row = storage.load'),
    }
    result = {name:dict(common,app=text) for name,text in entries.items()}
    result['effectful_packet'] = dict(common,app=source,model='''def build(rt, row, value):
    packet = (rt.read("extra", value), rt.add(row, 1))
    return packet
''',consumer='''def take(rt, packet):
    return packet[1]
''')
    return result
