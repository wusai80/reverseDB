"""Generic proposal generation and a separate local-step certificate checker."""
from copy import deepcopy
import json

from .model import (BACKEND_OPS, TOTAL_PURE, WIRE_TYPES, Contract,
                    Unsupported, bound, validate)


def consumers(program, value):
    return [op['id'] for op in program['ops'] if value in op['args']] + (['<output>'] if value in program['outputs'] else [])


def propose(program, contract=Contract()):
    """A fixed operator catalog; no names, labels, profiles or test data used."""
    validate(program)
    current, steps = deepcopy(program), []
    # Substitute projections of already constructed packets, preserving the
    # construction operands' earlier evaluation. Effects are separate ops.
    for original in list(program['ops']):
        ops = current['ops']; by_out = {op['out']:op for op in ops if 'out' in op}
        op = next((op for op in ops if op['id'] == original['id']), None)
        if op is None or op['op'] != 'field': continue
        producer = by_out.get(op['args'][0])
        if producer is None or producer['op'] != 'pack': continue
        replacement = producer['args'][op['index']]
        target = op['out']
        for other in ops: other['args'] = [replacement if a == target else a for a in other['args']]
        current['outputs'] = [replacement if a == target else a for a in current['outputs']]
        current['ops'] = [other for other in ops if other['id'] != op['id']]
        steps.append(dict(kind='project', site=op['id']))
    # Reverse order reaches the dead-value closure for this ordered SSA grammar.
    live = set(current['outputs']); kept = []
    for op in reversed(current['ops']):
        erasable = op['op'] in TOTAL_PURE or op['op'] == 'read' and not contract.read_errors_observable
        if erasable and op['out'] not in live:
            steps.append(dict(kind='erase', site=op['id']))
        else:
            kept.append(op); live.update(op['args'])
    current['ops'] = list(reversed(kept))
    return current, dict(schema='local-rewrite-certificate/v1', source=bound(program, contract), steps=steps)


def check(source, candidate, certificate, contract=Contract()):
    """Replay justified local equations; no appeal to candidate test outcomes.

    Deliberately does not invoke propose, its liveness analysis, or a supplied
    effect/capability flag. It rechecks immediate uses and types at every step.
    """
    try:
        validate(source); validate(candidate)
        if set(certificate) != {'schema', 'source', 'steps'} or certificate['schema'] != 'local-rewrite-certificate/v1':
            raise Unsupported('Malformed certificate')
        if certificate['source'] != bound(source, contract): raise Unsupported('Wrong source or execution contract')
        p = deepcopy(source)
        for step in certificate['steps']:
            if set(step) != {'kind', 'site'}: raise Unsupported('Unexpected proof-step fields')
            pos = next((i for i, o in enumerate(p['ops']) if o['id'] == step['site']), None)
            if pos is None: raise Unsupported('Missing proof-step site')
            op = p['ops'][pos]
            if step['kind'] == 'project':
                if op['op'] != 'field': raise Unsupported('Projection rule applied to another opcode')
                producer = next((o for o in p['ops'][:pos] if o.get('out') == op['args'][0]), None)
                if producer is None or producer['op'] != 'pack': raise Unsupported('Not a known packet producer')
                # Type validation establishes index validity and value semantics.
                replacement, target = producer['args'][op['index']], op['out']
                for later in p['ops'][pos+1:]:
                    later['args'] = [replacement if a == target else a for a in later['args']]
                p['outputs'] = [replacement if a == target else a for a in p['outputs']]
            elif step['kind'] == 'erase':
                if 'out' not in op or consumers(p, op['out']): raise Unsupported('Operation has a retained consumer')
                # Independent explicit case split of the semantic rules.
                if op['op'] not in {'const', 'eq', 'pack', 'field'}:
                    if op['op'] != 'read' or contract.read_errors_observable:
                        raise Unsupported('Dead result does not discharge effects or failures')
            else: raise Unsupported('Unknown proof rule')
            del p['ops'][pos]
            validate(p)
        # Python container equality conflates bool and int (True == 1).
        # Compare the complete typed JSON syntax, not a hash or loose equality.
        if json.dumps(p, sort_keys=True) != json.dumps(candidate, sort_keys=True):
            raise Unsupported('Candidate differs from certified result or order')
        return dict(accepted=True, checked_steps=len(certificate['steps']), authority='conditional IR semantics only')
    except (Unsupported, KeyError, TypeError, IndexError, ValueError) as error:
        return dict(accepted=False, reason=str(error))


def discover_regions(program, *, local_only=False):
    """Enumerate intervals; no automatic lowerer, placement or latency claim.

    Returned interfaces explain which cuts are supported by the fixed scalar
    wire catalog. Full packets remain valid internally. Emit stays at the host.
    """
    types = validate(program); ops = program['ops']; candidates = []
    for start in range(len(ops)):
        for stop in range(start+1, len(ops)+1):
            part = ops[start:stop]
            if not any(o['op'] in {'read', 'write'} for o in part): continue
            defined = {o['out'] for o in part if 'out' in o}
            incoming = {a for o in part for a in o['args']} - defined
            outside = set(program['outputs']) | {a for o in ops[stop:] for a in o['args']}
            outgoing = defined & outside
            reasons = []
            if any(o['op'] not in BACKEND_OPS for o in part): reasons.append('unsupported_target_operation')
            if any(types[v] not in WIRE_TYPES for v in incoming | outgoing): reasons.append('missing_boundary_codec')
            if local_only and len({o['module'] for o in part}) > 1: reasons.append('original_module_boundary')
            candidates.append(dict(start=start, stop=stop, inputs=sorted(incoming), outputs=sorted(outgoing),
                admissible=not reasons, reasons=reasons, generated_backend=False))
    return candidates
