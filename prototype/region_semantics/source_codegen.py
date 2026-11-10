"""Emit a direct Python replacement after source binding and rewrite checks.

This flattens verified direct helper calls and removes certified operations.
It does not batch SQL, remove SQLite subsystems, or implement remote placement.
"""
import ast
import hashlib

from .model import Unsupported, fingerprint
from .rewrite import check
from .source_frontend import verify_binding


def generate(sources,entry,input_types,source,witness,candidate,certificate,contract):
    if not verify_binding(sources,entry,input_types,source,witness):
        raise Unsupported('Source witness differs from re-extraction')
    admission = check(source,candidate,certificate,contract)
    if not admission['accepted']: raise Unsupported(admission['reason'])
    names = {name:'a'+str(i) for i,name in enumerate(candidate['inputs'])}
    lines = ['def replacement(rt, inputs):']
    for name,local in names.items(): lines.append('    '+local+' = inputs['+repr(name)+']')
    for index,op in enumerate(candidate['ops']):
        args = [names[x] for x in op['args']]; kind = op['op']
        if kind == 'const': code = repr(op['value'])
        elif kind == 'eq': code = args[0]+' == '+args[1]
        elif kind == 'pack': code = '('+', '.join(args)+(',' if args else '')+')'
        elif kind == 'field': code = args[0]+'['+repr(op['index'])+']'
        elif kind == 'add': code = 'rt.add('+', '.join(args)+')'
        elif kind in {'read','write'}: code = 'rt.'+kind+'('+', '.join([repr(op['id'])]+args)+')'
        elif kind == 'check': code = 'rt.check('+args[0]+', '+repr(op['code'])+')'
        elif kind == 'emit': code = 'rt.emit('+', '.join([repr(op['label'])]+args)+')'
        else: raise Unsupported('No code generation for opcode')
        if 'out' in op:
            local = 'v'+str(index); names[op['out']] = local
            code = local+' = '+code
        lines.append('    '+code)
    if len(candidate['outputs']) != 1: raise Unsupported('Source return must be one value, possibly a tuple')
    lines.append('    return '+names[candidate['outputs'][0]])
    text = '\n'.join(lines)+'\n'
    namespace = {'__builtins__':{}}
    exec(compile(ast.parse(text),'<checked-replacement>','exec'),namespace)
    record = dict(schema='closed-python-replacement/v1',source_sha256=witness['source_sha256'],
        ir_sha256=fingerprint(candidate),code_sha256=hashlib.sha256(text.encode()).hexdigest(),
        checked_rewrite=admission,sql_fusion=False,application_transfer=False)
    return namespace['replacement'],text,record
