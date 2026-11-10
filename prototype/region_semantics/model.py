"""A closed executable semantics; no arbitrary Python/SQL or effect flags.

This models ordered SSA operations, i64/bool/packet values, a total key-value
read, explicit failure, visible emit events, and one outer transaction.
It is not an abstraction extractor for existing applications.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

MIN_I64, MAX_I64 = -(2**63), 2**63-1
OPS = frozenset({'const', 'add', 'eq', 'pack', 'field', 'read', 'write', 'check', 'emit'})
TOTAL_PURE = frozenset({'const', 'eq', 'pack', 'field'})
BACKEND_OPS = OPS - {'emit'}
WIRE_TYPES = frozenset({'i64', 'bool'})


class Unsupported(ValueError):
    pass


class OutOfDomain(ValueError):
    pass


class Failure(Exception):
    pass


@dataclass(frozen=True)
class Contract:
    # False excludes read faults globally; it does not infer their absence.
    read_errors_observable: bool = True

    def __post_init__(self):
        if type(self.read_errors_observable) is not bool:
            raise OutOfDomain('Contract flag must be boolean')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scalar_type(value):
    if type(value) is bool:
        return 'bool'
    if type(value) is int and MIN_I64 <= value <= MAX_I64:
        return 'i64'
    raise Unsupported('Only exact booleans and signed 64-bit integers are literals')


def validate(program):
    """Derive types and failure eligibility from opcodes, never user claims."""
    if not isinstance(program, dict) or set(program) != {'inputs', 'ops', 'outputs'}:
        raise Unsupported('Program fields differ from the closed grammar')
    if not isinstance(program['inputs'], dict) or not isinstance(program['ops'], list):
        raise Unsupported('Inputs must be a type map and operations an ordered list')
    types = dict(program['inputs'])
    if any(not isinstance(k, str) or v not in WIRE_TYPES for k, v in types.items()):
        raise Unsupported('Unsupported input type')
    ids = set()
    for op in program['ops']:
        if not isinstance(op, dict) or not {'id', 'op', 'args', 'module'} <= op.keys():
            raise Unsupported('Incomplete instruction')
        kind = op['op']
        extras = {'value'} if kind == 'const' else {'index'} if kind == 'field' else {'code'} if kind == 'check' else {'label'} if kind == 'emit' else set()
        returns = kind in {'const', 'add', 'eq', 'pack', 'field', 'read'}
        if kind not in OPS or set(op) != {'id', 'op', 'args', 'module'} | extras | ({'out'} if returns else set()):
            raise Unsupported('Unknown opcode or unauthorized semantic metadata')
        if not isinstance(op['id'], str) or op['id'] in ids or not isinstance(op['module'], str):
            raise Unsupported('Invalid instruction identity or source module')
        ids.add(op['id'])
        if not isinstance(op['args'], list) or any(not isinstance(a, str) or a not in types for a in op['args']):
            raise Unsupported('Unresolved/non-SSA operand')
        args = [types[a] for a in op['args']]
        result = None
        if kind == 'const':
            if args: raise Unsupported('Constant has operands')
            result = scalar_type(op['value'])
        elif kind in {'add', 'eq'}:
            if args != ['i64', 'i64']: raise Unsupported('Arithmetic/comparison operands must be i64')
            result = 'i64' if kind == 'add' else 'bool'
        elif kind == 'pack':
            result = tuple(args)
        elif kind == 'field':
            index = op['index']
            if len(args) != 1 or not isinstance(args[0], tuple) or type(index) is not int or not 0 <= index < len(args[0]):
                raise Unsupported('Packet access is not statically total')
            result = args[0][index]
        elif kind == 'read':
            if args != ['i64']: raise Unsupported('Read key must be i64')
            result = 'i64'
        elif kind == 'write':
            if args != ['i64', 'i64']: raise Unsupported('Write key/value must be i64')
        elif kind == 'check':
            if args != ['bool'] or not isinstance(op['code'], str): raise Unsupported('Invalid guard')
        elif kind == 'emit':
            if not isinstance(op['label'], str): raise Unsupported('Invalid observable event')
        if returns:
            if not isinstance(op['out'], str) or op['out'] in types: raise Unsupported('Duplicate SSA definition')
            types[op['out']] = result
    if not isinstance(program['outputs'], list) or any(v not in types for v in program['outputs']):
        raise Unsupported('Unknown observable output')
    return types


def domain(program, inputs, state, faults, contract):
    validate(program)
    if type(contract.read_errors_observable) is not bool:
        raise OutOfDomain('Contract flag must be boolean')
    if set(inputs) != set(program['inputs']): raise OutOfDomain('Input interface mismatch')
    for key, value in inputs.items():
        if scalar_type(value) != program['inputs'][key]: raise OutOfDomain('Input type mismatch')
    for key, value in state.items():
        if scalar_type(key) != 'i64' or scalar_type(value) != 'i64': raise OutOfDomain('Store is not i64 to i64')
    by_id = {op['id']:op for op in program['ops']}
    for ident, code in faults.items():
        if ident not in by_id or by_id[ident]['op'] not in {'read', 'write'} or not isinstance(code, str):
            raise OutOfDomain('Fault site is not a database operation')
        if by_id[ident]['op'] == 'read' and not contract.read_errors_observable:
            raise OutOfDomain('Read failures excluded by this contract')


def observe(program, inputs, state, faults=None, contract=Contract()):
    """Reference operational semantics. Failed executions roll back all writes.

    Emit events are external and survive rollback. Resource exhaustion,
    tracing, receiver identity and concurrent observations are not modeled.
    """
    faults = faults or {}
    domain(program, inputs, state, faults, contract)
    env, store, events = dict(inputs), dict(state), []
    stats = dict(reads=0, writes=0, packets=0)
    try:
        for op in program['ops']:
            kind = op['op']; args = [env[a] for a in op['args']]
            if kind in {'read', 'write'} and op['id'] in faults: raise Failure(faults[op['id']])
            if kind == 'const': value = op['value']
            elif kind == 'add':
                value = args[0] + args[1]
                if not MIN_I64 <= value <= MAX_I64: raise Failure('i64-overflow')
            elif kind == 'eq': value = args[0] == args[1]
            elif kind == 'pack': value = tuple(args); stats['packets'] += 1
            elif kind == 'field': value = args[0][op['index']]
            elif kind == 'read': value = store.get(args[0], 0); stats['reads'] += 1
            elif kind == 'write': store[args[0]] = args[1]; stats['writes'] += 1
            elif kind == 'check':
                if not args[0]: raise Failure(op['code'])
            elif kind == 'emit': events.append((op['label'], tuple(args)))
            if 'out' in op: env[op['out']] = value
        outcome = ('ok', tuple(env[v] for v in program['outputs']))
    except Failure as error:
        store = dict(state); outcome = ('error', str(error))
    return dict(observation=dict(outcome=outcome, state=store, events=events), work=stats)


def bound(program, contract):
    return fingerprint(dict(program=program, contract=asdict(contract), semantics='ordered-i64-packets/v1'))
