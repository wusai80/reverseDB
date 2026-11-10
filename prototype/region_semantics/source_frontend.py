"""Closed Python source bundle -> frozen v1 IR; never import application code.

Supported source is deliberately narrower than Python: module-level functions,
closed-bundle imports, direct helper calls, scalar literals, tuple values and
literal tuple indexing, equality, and an explicit trusted runtime interface.
The resulting witness binds exact source bytes, not arbitrary live objects.
"""
import ast
import hashlib
import json

from .model import Unsupported, fingerprint, validate

RESOURCE = object()


class Frontend:
    def __init__(self, sources):
        if not sources or any(not k.isidentifier() for k in sources):
            raise Unsupported('Bundle requires simple module names')
        self.sources = dict(sources)
        self.modules, self.functions, self.imports = {}, {}, {}
        self.ops, self.locations, self.edges = [], {}, []
        self.serial = 0
        for module, text in self.sources.items():
            tree = ast.parse(text, filename=module+'.py')
            self.modules[module] = tree
            imports, functions = {}, {}
            for n in tree.body:
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                    continue
                if isinstance(n, ast.Import):
                    for alias in n.names:
                        if alias.name not in sources: raise Unsupported('Import outside closed source bundle')
                        local = alias.asname or alias.name
                        if local in imports or local in functions: raise Unsupported('Module binding is overwritten')
                        imports[local] = alias.name
                elif isinstance(n, ast.FunctionDef):
                    a = n.args
                    if n.name in imports or n.name in functions: raise Unsupported('Duplicate module binding')
                    if n.decorator_list or n.returns or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or any(x.annotation for x in a.args) or not a.args:
                        raise Unsupported('Unsupported function signature/decorator')
                    if len({x.arg for x in a.args}) != len(a.args):
                        raise Unsupported('Duplicate function parameter')
                    functions[n.name] = n
                else: raise Unsupported('Executable module state or class definitions are unsupported')
            self.imports[module], self.functions[module] = imports, functions

    def instruction(self, kind, args, node, module, path, **extra):
        if any(a is RESOURCE or a is None for a in args):
            raise Unsupported('Resource/void value escapes into an ordinary operation')
        serial = self.serial; self.serial += 1
        ident = extra.pop('site', 'op:'+str(serial))
        ins = dict(id=ident, op=kind, args=args, module=module, **extra)
        result = None
        if kind in {'const','add','eq','pack','field','read'}:
            result = 'v'+str(serial); ins['out'] = result
        self.ops.append(ins)
        self.locations[ident] = dict(module=module, line=node.lineno, column=node.col_offset, call_path=[list(x) for x in path])
        return result

    def call(self, module, name, actuals, path):
        target = (module, name)
        if target in path: raise Unsupported('Recursive helper call')
        fn = self.functions.get(module, {}).get(name)
        if fn is None: raise Unsupported('Unknown direct function')
        params = [a.arg for a in fn.args.args]
        if len(actuals) != len(params) or actuals[0] is not RESOURCE:
            raise Unsupported('Every helper requires the exact runtime as its first argument')
        globals_ = set(self.functions[module]) | set(self.imports[module])
        if set(params) & globals_: raise Unsupported('Parameter shadows a module binding')
        env = dict(zip(params, actuals)); path = path+(target,)
        body = list(fn.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str): body = body[1:]
        if not body or not isinstance(body[-1], ast.Return): raise Unsupported('Function needs one terminal return')
        for stmt in body[:-1]:
            if isinstance(stmt, ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0], ast.Name):
                local = stmt.targets[0].id
                # This also avoids Python's lexical UnboundLocalError when a
                # later assignment shadows a global helper used earlier.
                if local in globals_ or local == params[0]: raise Unsupported('Assignment shadows a callable or runtime')
                value = self.expr(stmt.value, env, module, path)
                if value is RESOURCE or value is None: raise Unsupported('Resource alias or void assignment')
                env[local] = value
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                self.expr(stmt.value, env, module, path)
            else: raise Unsupported('Unsupported statement: '+type(stmt).__name__)
        result = self.expr(body[-1].value, env, module, path)
        if result is RESOURCE or result is None: raise Unsupported('Resource/void return')
        return result

    @staticmethod
    def literal(node, expected):
        if not isinstance(node, ast.Constant) or type(node.value) is not expected:
            raise Unsupported('Runtime metadata must be a literal '+expected.__name__)
        return node.value

    def expr(self, n, env, module, path):
        def emit(kind, args, **extra): return self.instruction(kind,args,n,module,path,**extra)
        if isinstance(n, ast.Name):
            if n.id not in env: raise Unsupported('Unresolved local value')
            return env[n.id]
        if isinstance(n, ast.Constant): return emit('const', [], value=n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant) and type(n.operand.value) is int:
            return emit('const', [], value=-n.operand.value)
        if isinstance(n, ast.Tuple): return emit('pack', [self.expr(e,env,module,path) for e in n.elts])
        if isinstance(n, ast.Subscript):
            return emit('field', [self.expr(n.value,env,module,path)], index=self.literal(n.slice,int))
        if isinstance(n, ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0], ast.Eq):
            return emit('eq', [self.expr(n.left,env,module,path), self.expr(n.comparators[0],env,module,path)])
        if isinstance(n, ast.Call) and not n.keywords:
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and env.get(f.value.id) is RESOURCE:
                kind = f.attr
                if kind in {'read','write'} and len(n.args)==(2 if kind=='read' else 3):
                    site = self.literal(n.args[0],str)
                    if site.startswith('op:'): raise Unsupported('Reserved site prefix')
                    return emit(kind,[self.expr(a,env,module,path) for a in n.args[1:]],site=site)
                if kind == 'add' and len(n.args)==2:
                    return emit('add',[self.expr(a,env,module,path) for a in n.args])
                if kind == 'check' and len(n.args)==2:
                    return emit('check',[self.expr(n.args[0],env,module,path)],code=self.literal(n.args[1],str))
                if kind == 'emit' and n.args:
                    label = self.literal(n.args[0],str)
                    return emit('emit',[self.expr(a,env,module,path) for a in n.args[1:]],label=label)
                raise Unsupported('Unknown runtime operation/signature')
            target = None
            if isinstance(f, ast.Name) and f.id not in env:
                target = (module,f.id)
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id not in env and f.value.id in self.imports[module]:
                target = (self.imports[module][f.value.id],f.attr)
            if target:
                actuals = [self.expr(a,env,module,path) for a in n.args]
                self.edges.append(dict(caller=list(path[-1]), callee=list(target), line=n.lineno))
                return self.call(*target,actuals,path)
            raise Unsupported('Dynamic/indirect callable')
        raise Unsupported('Unsupported expression: '+type(n).__name__)


def extract(sources, entry, input_types):
    frontend = Frontend(sources)
    module, name = entry
    fn = frontend.functions.get(module,{}).get(name)
    if fn is None: raise Unsupported('Missing entry')
    params = [a.arg for a in fn.args.args]
    if set(params[1:]) != set(input_types): raise Unsupported('Input binding differs from source signature')
    # Generated SSA values cannot collide with the input variable namespace.
    bindings = {name:'arg:'+name for name in params[1:]}
    output = frontend.call(module,name,[RESOURCE]+list(bindings.values()),())
    program = dict(inputs={bindings[k]:v for k,v in input_types.items()},ops=frontend.ops,outputs=[output])
    validate(program)
    witness = dict(schema='closed-python-extraction/v1',entry=list(entry),input_types=dict(input_types),parameter_order=params[1:],
        inputs=bindings,source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
        ir_sha256=fingerprint(program),locations=frontend.locations,call_edges=frontend.edges,
        premises=['exact closed source bundle', 'exact trusted runtime', 'typed scalar inputs',
                  'structural tuple observations; no identity/frame/refcount/resource observations',
                  'serial execution and the frozen IR transaction/failure domain'])
    return program,witness


def verify_binding(sources,entry,input_types,program,witness):
    """Re-extract to bind bytes and structure. Not an independent semantics proof."""
    try:
        expected, record = extract(sources,entry,input_types)
        return (json.dumps(expected,sort_keys=True)==json.dumps(program,sort_keys=True)
                and json.dumps(record,sort_keys=True)==json.dumps(witness,sort_keys=True))
    except (Unsupported,SyntaxError,ValueError,TypeError):
        return False
