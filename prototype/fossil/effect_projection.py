"""Source-bound native experiment: erase private formatting, retain effects.

This is a reviewed backend adapter, NOT admission by the scalar IR or the DP.
The pinned source contract supplies C lifetime/effect premises; Clang supplies
locations and def/use evidence. Unknown versions and conversions are refused.
"""
import hashlib
import json
import re


def walk(node, parents=()):
    yield node, parents
    for child in node.get('inner', []):
        yield from walk(child, parents + (node,))


def span(node):
    r = node['range']
    return r['begin']['offset'], r['end']['offset'] + r['end']['tokLen']


def source_of(source, node):
    a, b = span(node)
    return source[a:b].decode()


def parse_ast(text, name):
    decoder = json.JSONDecoder()
    found = []
    while text.strip():
        text = text.lstrip()
        node, end = decoder.raw_decode(text)
        text = text[end:]
        if node.get('name') == name and any(c['kind'] == 'CompoundStmt' for c in node.get('inner', [])):
            found.append(node)
    if len(found) != 1:
        raise ValueError('Expected one function definition')
    return found[0]


def format_effects(fmt, args):
    """Closed, ordered effect projection; never assume a foreign call is pure."""
    if len(fmt.encode()) >= 1024:
        raise ValueError('Format exceeds reviewed size bound')
    conversions = []
    i = 0
    while i < len(fmt):
        if fmt[i] == '%':
            i += 1
            if i == len(fmt) or fmt[i] not in 'sS%':
                raise ValueError('Unknown formatter effect')
            if fmt[i] != '%':
                conversions.append(fmt[i])
        i += 1
    if len(conversions) != len(args) or len(args) > 3:
        raise ValueError('Argument shape outside reviewed bound')
    return [f'(({arg}) ? (void)hash_digits(0) : (void)0)'
            for kind, arg in zip(conversions, args) if kind == 'S']


# A fresh Blob with <= 3 strings of < 65536 bytes and < 1024 literal bytes
# cannot approach MAX_BLOB_SIZE (0x7fff0000), even with geometric growth.
# Larger inputs use the ORIGINAL call, preserving its deterministic size error.
HELPER = b'''static int reverse_db_small_string(const char *s){
  unsigned i;
  if( s==0 ) return 1;
  for(i=0; i<65536; ++i){ if( s[i]==0 ) return 1; }
  return 0;
}

'''


def transform(source, function, binding, preserve_effects=True):
    if hashlib.sha256(source).hexdigest() != binding['timeline_sha256']:
        raise ValueError('Source differs from reviewed contract')
    declarations = [n for n, _ in walk(function) if n['kind'] == 'VarDecl' and n.get('name') == 'zFree']
    if len(declarations) != 1:
        raise ValueError('Ambiguous buffer')
    ident = declarations[0]['id']
    refs = [(n, p) for n, p in walk(function)
            if n['kind'] == 'DeclRefExpr' and n['referencedDecl']['id'] == ident]
    edits, evidence, consumers = [], [], []
    for ref, parents in refs:
        parent = parents[-1]
        if parent['kind'] == 'BinaryOperator' and parent.get('opcode') == '=' and parent['inner'][0]['id'] == ref['id']:
            call = parent['inner'][1]
            if call['kind'] != 'CallExpr' or source_of(source, call['inner'][0]) != 'mprintf':
                raise ValueError('Unknown producer')
            literals = [n for n, _ in walk(call['inner'][1]) if n['kind'] == 'StringLiteral']
            if len(literals) != 1:
                raise ValueError('Nonconstant format')
            fmt = json.loads(literals[0]['value'])
            args = [source_of(source, n) for n in call['inner'][2:]]
            # The reviewed adapter accepts local reads and this guarded +1 only.
            for arg in call['inner'][2:]:
                for n, _ in walk(arg):
                    if n['kind'] not in {'ImplicitCastExpr', 'DeclRefExpr', 'BinaryOperator', 'IntegerLiteral'}:
                        raise ValueError('Effectful argument')
                    if n['kind'] == 'BinaryOperator' and n.get('opcode') != '+':
                        raise ValueError('Unreviewed argument expression')
                    if 'volatile' in n.get('type', {}).get('qualType', ''):
                        raise ValueError('Volatile argument')
            effects = format_effects(fmt, args)
            eligibility = ' && '.join(f'reverse_db_small_string({a})' for a in args)
            projection = ', '.join((effects if preserve_effects else []) + ['(char *)0'])
            replacement = f'(zFormat && {eligibility} ? ({projection}) : {source_of(source, call)})'
            a, b = span(call)
            edits.append((a, b, replacement.encode()))
            evidence.append(dict(span=[a, b], format=fmt, arguments=args, retained_effects=effects))
        else:
            calls = [p for p in parents if p['kind'] == 'CallExpr']
            if len(calls) != 1:
                raise ValueError('Buffer escapes')
            callee = source_of(source, calls[0]['inner'][0])
            if callee == 'comment_print':
                guards = [p for p in parents if p['kind'] == 'IfStmt' and len(p.get('inner', [])) == 3
                          and source_of(source, p['inner'][0]) == 'zFormat']
                if len(guards) != 1 or not any(p['id'] == guards[0]['inner'][2]['id'] for p in parents):
                    raise ValueError('Live buffer consumer on erased path')
            elif callee != 'fossil_free':
                raise ValueError('Unreviewed buffer consumer')
            consumers.append(callee)
    if len(edits) != 4 or sorted(consumers) != ['comment_print', 'fossil_free']:
        raise ValueError('Def/use shape changed')
    # Binding pins enclosing control flow: no guard mutation, aliases, extra
    # assignments or stores; effects stay at each original call site.
    start, _ = span(function)
    edits.append((start, start, HELPER))
    result = source
    for a, b, replacement in sorted(edits, reverse=True):
        result = result[:a] + replacement + result[b:]
    return result, dict(producers=evidence, consumers=consumers,
                        authority='Reviewed pinned C contract; not general native proof',
                        effect_projection_enabled=preserve_effects)
