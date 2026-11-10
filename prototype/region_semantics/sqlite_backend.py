"""Independent primitive executor, not a fused SQL compiler.

Only interface/domain validation is shared with the reference interpreter.
Unexpected SQLite/environment errors fail the experiment; they are not
silently equated with a modeled fault or used as evidence of equivalence.
"""
import sqlite3

from .model import Contract, domain


def execute(program, inputs, state, faults=None, contract=Contract()):
    faults = faults or {}
    domain(program, inputs, state, faults, contract)
    db = sqlite3.connect(':memory:', isolation_level=None)
    try:
        db.execute('CREATE TABLE kv (k INTEGER PRIMARY KEY, v INTEGER NOT NULL)')
        db.executemany('INSERT INTO kv VALUES (?, ?)', state.items())
        db.execute('BEGIN')
        values, emitted = dict(inputs), []
        work = dict(reads=0, writes=0, packets=0)
        error = None
        for ins in program['ops']:
            tag = ins['op']
            operands = [values[name] for name in ins['args']]
            if tag in ('read', 'write') and ins['id'] in faults:
                error = faults[ins['id']]
                break
            if tag == 'const': result = ins['value']
            elif tag == 'add':
                result = sum(operands)
                if result < -9223372036854775808 or result > 9223372036854775807:
                    error = 'i64-overflow'
                    break
            elif tag == 'eq': result = operands[0] == operands[1]
            elif tag == 'pack':
                result = tuple(operands)
                work['packets'] += 1
            elif tag == 'field': result = operands[0][ins['index']]
            elif tag == 'read':
                row = db.execute('SELECT v FROM kv WHERE k = ?', (operands[0],)).fetchone()
                result = 0 if row is None else row[0]
                work['reads'] += 1
            elif tag == 'write':
                db.execute('INSERT INTO kv VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', operands)
                work['writes'] += 1
            elif tag == 'check':
                if operands[0] is False:
                    error = ins['code']
                    break
            elif tag == 'emit': emitted.append((ins['label'], tuple(operands)))
            if 'out' in ins: values[ins['out']] = result
        if error is None:
            db.execute('COMMIT')
            outcome = ('ok', tuple(values[name] for name in program['outputs']))
        else:
            db.execute('ROLLBACK')
            outcome = ('error', error)
        complete_state = dict(db.execute('SELECT k, v FROM kv ORDER BY k'))
        return dict(observation=dict(outcome=outcome, state=complete_state, events=emitted), work=work)
    finally:
        db.close()
