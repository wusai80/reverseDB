"""Execute original closed Python functions and generated replacements.

SQLite stays in one outer transaction. The runtime is an explicit trusted
primitive API, not a proxy for arbitrary Python receivers or database drivers.
"""
import ast
import sqlite3
import types

from .model import Contract, Failure, Unsupported, domain
from .source_frontend import Frontend, verify_binding


class Runtime:
    def __init__(self, db, faults):
        self.db, self.faults = db, faults
        self.events = []
        self.reads, self.writes = 0, 0

    def read(self, site, key):
        if site in self.faults: raise Failure(self.faults[site])
        row = self.db.execute('SELECT v FROM kv WHERE k=?',(key,)).fetchone()
        self.reads += 1
        return 0 if row is None else row[0]

    def write(self, site, key, value):
        if site in self.faults: raise Failure(self.faults[site])
        self.db.execute('INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(key,value))
        self.writes += 1

    @staticmethod
    def add(a,b):
        result = a+b
        if not -(1<<63) <= result < (1<<63): raise Failure('i64-overflow')
        return result

    @staticmethod
    def check(value, code):
        if not value: raise Failure(code)

    def emit(self,label,*values): self.events.append((label,values))


def load_original(sources,entry,input_types,program,witness):
    if not verify_binding(sources,entry,input_types,program,witness):
        raise Unsupported('Source/IR witness is stale or altered')
    frontend = Frontend(sources)
    modules = {name:types.ModuleType(name) for name in sources}
    for name, tree in frontend.modules.items():
        namespace = modules[name].__dict__
        namespace['__builtins__'] = {}
        namespace.update({alias:modules[target] for alias,target in frontend.imports[name].items()})
        # Imports are resolved to the already bound bundle, never sys.path.
        # Function bodies are otherwise executed as original Python source.
        body = [node for node in tree.body if not isinstance(node,ast.Import)]
        code = compile(ast.Module(body=body,type_ignores=[]),name+'.py','exec')
        exec(code,namespace)
    return modules[entry[0]].__dict__[entry[1]]


def invoke(function,program,witness,inputs,state,faults=None,contract=Contract(),*,generated=False):
    """Typed, closed harness; unexpected Python/SQLite errors propagate."""
    faults = faults or {}
    if set(inputs) != set(witness['inputs']): raise Unsupported('Input names differ from bound entry')
    ir_inputs = {v:inputs[k] for k,v in witness['inputs'].items()}
    domain(program,ir_inputs,state,faults,contract)
    db = sqlite3.connect(':memory:',isolation_level=None)
    try:
        db.execute('CREATE TABLE kv(k INTEGER PRIMARY KEY,v INTEGER NOT NULL)')
        db.executemany('INSERT INTO kv VALUES(?,?)',state.items())
        rt = Runtime(db,faults)
        db.execute('BEGIN')
        try:
            result = function(rt,ir_inputs) if generated else function(rt,*inputs_in_order(inputs,witness))
            outcome = ('ok',(result,))
            db.execute('COMMIT')
        except Failure as error:
            db.execute('ROLLBACK')
            outcome = ('error',str(error))
        return dict(observation=dict(outcome=outcome,state=dict(db.execute('SELECT k,v FROM kv')),events=rt.events),
                    work=dict(reads=rt.reads,writes=rt.writes))
    finally:
        db.close()


def inputs_in_order(inputs,witness):
    return [inputs[k] for k in witness['parameter_order']]
