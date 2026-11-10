"""Closed effectful extension composed with the existing scalar rewrite checker.

The scalar projection is an ANALYSIS encoding, never executed as DB reads.
Every extension op is retained as ordered use+may-fail barriers. Its complete
metadata is encoded in the barrier label. No inferred callback purity exists.
"""
from copy import deepcopy
import json

from prototype.region_semantics import model, rewrite
from .lowering import Unsupported,compile_demand,prepare
from .ownership import constructor_binding

ANCHOR='@effect-anchor'
NEW={'lazy_new','lazy_get','invoke'}


def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'))


def project(program):
    p=deepcopy(program);p['inputs'][ANCHOR]='i64';ops=[]
    for o in p['ops']:
        if o['op'] in NEW:
            meta={k:v for k,v in o.items() if k!='args'}
            ops.append(dict(id='@use:'+o['id'],op='emit',module=o['module'],args=o['args'],label=canonical(meta)))
            ops.append(dict(id=o['id'],op='read',module=o['module'],args=[ANCHOR],out=o['out']))
        else:ops.append(o)
    p['ops']=ops
    return p


def validate(program,catalog):
    if set(program)!={'inputs','ops','outputs'} or not isinstance(program['ops'],list):raise Unsupported('Closed program grammar required')
    if any(not isinstance(k,str) or k.startswith('@') or v not in {'i64','bool'} for k,v in program['inputs'].items()):raise Unsupported('Only scalar public inputs')
    types=dict(program['inputs']);ids=set()
    for o in program['ops']:
        if not {'id','op','module','args'}<=o.keys() or not isinstance(o['id'],str) or o['id'].startswith('@') or o['id'] in ids:raise Unsupported('Invalid/reserved operation identity')
        ids.add(o['id'])
        if not isinstance(o['args'],list) or any(a not in types for a in o['args']):raise Unsupported('Unresolved operands')
        args=[types[a] for a in o['args']];kind=o['op']
        if kind in NEW:
            extra={'binding','raw','converter'} if kind=='lazy_new' else {'key'} if kind=='lazy_get' else {'callee'}
            if set(o)!={'id','op','module','args','out'}|extra:raise Unsupported('Unknown extension metadata')
            if kind=='lazy_new':
                if args or o['binding'] not in catalog['bindings'] or o['raw'] not in catalog['raws'] or o['converter'] not in catalog['converters']:raise Unsupported('Unknown lazy allocation resource')
                typ=('private',o['id'])
            elif kind=='lazy_get':
                if len(args)!=1 or not isinstance(args[0],tuple) or args[0][0]!='private' or type(o['key']) is not str:raise Unsupported('Demand needs one private handle and exact string key')
                typ='value'
            else:
                if o['callee'] not in catalog['calls'] or any(is_private(t) for t in args):raise Unsupported('Unknown callback or private handle passed to callback')
                typ='value'
        else:
            if any(is_private(t) for t in args):raise Unsupported('Private handle cannot enter ordinary operations')
            if kind=='const':typ=model.scalar_type(o['value'])
            elif kind in {'add','eq'}:
                if args!=['i64','i64']:raise Unsupported('Opaque converted value used as scalar')
                typ='i64' if kind=='add' else 'bool'
            elif kind=='pack':typ=('packet',tuple(args))
            elif kind=='field':
                j=o['index']
                if len(args)!=1 or not isinstance(args[0],tuple) or args[0][0]!='packet' or type(j) is not int or not 0<=j<len(args[0][1]):raise Unsupported('Invalid packet field')
                typ=args[0][1][j]
            elif kind=='read':
                if args!=['i64']:raise Unsupported('Invalid database key')
                typ='i64'
            elif kind=='write':
                if args!=['i64','i64']:raise Unsupported('Invalid database write')
            elif kind=='check':
                if args!=['bool']:raise Unsupported('Opaque guard needs explicit callback')
            elif kind!='emit':raise Unsupported('Unknown opcode')
        if 'out' in o:
            if not isinstance(o['out'],str) or o['out'].startswith('@') or o['out'] in types:raise Unsupported('Duplicate/reserved SSA value')
            types[o['out']]=typ
    if any(x not in types or is_private(types[x]) for x in program['outputs']):raise Unsupported('Private state escapes through output')
    # Scalar grammar/type checker also checks original opcode fields and shape.
    model.validate(project(program))
    return types


def is_private(typ):return isinstance(typ,tuple) and typ[0]=='private'


def catalog_record(bindings,raws,converters,calls):
    # The runtime supplies named resources, never candidate-supplied purity flags.
    return dict(bindings={k:constructor_binding(cls)[1] for k,cls in bindings.items()},
                raws=sorted(raws),converters=sorted(converters),calls=sorted(calls))


def propose(program,catalog):
    validate(program,catalog)
    _,inner=rewrite.propose(project(program),model.Contract(True))
    q=deepcopy(program)
    # Lift only the old checker's two elementary syntax operations.
    for step in inner['steps']:
        op=next(o for o in q['ops'] if o['id']==step['site'])
        if step['kind']=='project':
            producer=next(o for o in q['ops'] if o.get('out')==op['args'][0])
            value=producer['args'][op['index']];target=op['out']
            for later in q['ops']:later['args']=[value if a==target else a for a in later['args']]
            q['outputs']=[value if a==target else a for a in q['outputs']]
        q['ops'].remove(op)
    cert=dict(schema='private-lazy-ir/v1',source=model.fingerprint(program),catalog=model.fingerprint(catalog),scalar_certificate=inner)
    return q,cert


def check(source,candidate,certificate,catalog):
    try:
        validate(source,catalog);validate(candidate,catalog)
        if set(certificate)!={'schema','source','catalog','scalar_certificate'} or certificate['schema']!='private-lazy-ir/v1' or certificate['source']!=model.fingerprint(source) or certificate['catalog']!=model.fingerprint(catalog):raise Unsupported('Wrong source/catalog certificate')
        verdict=rewrite.check(project(source),project(candidate),certificate['scalar_certificate'],model.Contract(True))
        if not verdict['accepted']:raise Unsupported(verdict['reason'])
        return dict(accepted=True,private_state='no handle escapes through the supported IR',callback_purity_inferred=False)
    except (ValueError,TypeError,KeyError,IndexError) as e:return dict(accepted=False,reason=str(e))


def compile_program(source,candidate,certificate,catalog,bindings,raws,converters,calls,*,original=False):
    actual=catalog_record(bindings,raws,converters,calls)
    if canonical(actual)!=canonical(catalog):raise Unsupported('Runtime binding/source catalog changed')
    verdict=check(source,candidate,certificate,catalog)
    if not verdict['accepted']:raise Unsupported(verdict['reason'])
    # Freeze resource maps; callers cannot add another capability after admission.
    raws=dict(raws);converters=dict(converters);calls=dict(calls);bindings=dict(bindings)
    loaders={k:compile_demand(constructor_binding(c)[0]) for k,c in bindings.items()}
    names={k:'a'+str(i) for i,k in enumerate(sorted(candidate['inputs']))}
    lines=['def run(rt, inputs):']
    for key,name in names.items():lines.append('    '+name+' = inputs['+repr(key)+']')
    for i,o in enumerate(candidate['ops']):
        args=[names[a] for a in o['args']];kind=o['op']
        if kind=='lazy_new':
            code='new('+', '.join(repr(o[k]) for k in ['binding','raw','converter'])+')'
        elif kind=='lazy_get':code='get('+args[0]+', '+repr(o['key'])+')'
        elif kind=='invoke':code='calls['+repr(o['callee'])+']('+', '.join(args)+')'
        elif kind=='const':code=repr(o['value'])
        elif kind=='pack':code='('+', '.join(args)+(',' if args else '')+')'
        elif kind=='field':code=args[0]+'['+repr(o['index'])+']'
        elif kind=='eq':code=args[0]+' == '+args[1]
        elif kind=='add':code='rt.add('+', '.join(args)+')'
        elif kind in {'read','write'}:code='rt.'+kind+'('+', '.join([repr(o['id'])]+args)+')'
        elif kind=='check':code='rt.check('+args[0]+', '+repr(o['code'])+')'
        elif kind=='emit':code='rt.emit('+', '.join([repr(o['label'])]+args)+')'
        else:raise Unsupported('No code emitter')
        if 'out' in o:
            names[o['out']]='v'+str(i);code=names[o['out']]+' = '+code
        lines.append('    '+code)
    lines.append('    return ('+', '.join(names[v] for v in candidate['outputs'])+(',' if candidate['outputs'] else '')+')')
    code='\n'.join(lines)+'\n'
    def new(binding,raw,converter):
        r=raws[raw];f=converters[converter];prepare(r,f)
        return (bindings[binding](r,f),None) if original else (prepare(r,f),loaders[binding])
    def get(state,key):
        obj,loader=state
        return obj[key] if loader is None else loader(*obj,key)
    ns={'__builtins__':{},'new':new,'get':get,'calls':calls}
    exec(compile(code,'<checked-private-lazy-ir>','exec'),ns)
    fn=ns['run']
    def run(rt,inputs):
        if set(inputs)!=set(candidate['inputs']) or any(model.scalar_type(inputs[k])!=typ for k,typ in candidate['inputs'].items()):raise Unsupported('Public input type mismatch')
        return fn(rt,inputs)
    return run,code,verdict
