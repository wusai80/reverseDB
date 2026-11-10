"""Small, source-name-independent SQL value/effect representation.

This describes declared single-table effects, not SQL execution semantics or
whole-program equivalence. Existing Java AST/value recovery stays unchanged.
"""
import re
from cross_layer_dependencies import tokens, condense
from jdbc_boundary_contracts import inspect_binding

IDENT = re.compile(r'[a-z_][a-z_0-9]*\Z')
PRECEDENCE = {'or': 1, 'and': 2, '=': 3, '<>': 3, '<': 3, '>': 3,
              '<=': 3, '>=': 3, 'is': 3, '+': 4, '-': 4, '*': 5}
CLAUSES = {'from', 'where', 'order', 'limit', 'for', 'as', 'asc', 'desc',
           'join', 'inner', 'left', 'right', 'union', 'group', 'having',
           'returning', 'on', 'offset', 'select', 'insert', 'update', 'delete',
           'into', 'values', 'set', 'distinct', 'default', 'case', 'when', 'then', 'else', 'end'}


class Parser:
    def __init__(self, sql):
        raw=tokens(sql); self.t=[]; slot=0; i=0
        while i<len(raw):
            x=raw[i]
            if x=='?': slot+=1; x='@'+str(slot)
            elif x in ('<','>') and i+1<len(raw) and raw[i+1] in ('=','>'):
                x+=raw[i+1]; i+=1
            self.t.append(x); i+=1
        if self.t and self.t[-1]==';': self.t.pop()
        if ';' in self.t: raise ValueError('multiple statements unsupported')
        self.i=0; self.parameters=slot

    def peek(self): return self.t[self.i] if self.i<len(self.t) else ''
    def take(self, expected=None):
        value=self.peek()
        if not value or expected is not None and value!=expected:
            raise ValueError('expected '+str(expected)+', got '+repr(value))
        self.i+=1; return value
    def name(self):
        value=self.take()
        if not IDENT.fullmatch(value) or value in CLAUSES or value in PRECEDENCE:
            raise ValueError('unsupported identifier '+value)
        return value
    def qualified(self):
        value=self.name()
        if self.peek()=='.': self.take(); value+='.'+self.name()
        return value

    def expression(self, minimum=0):
        token=self.take()
        if token in ('-', '+', 'not'):
            left=dict(op='unary', operator=token, value=self.expression(3 if token=='not' else 6))
        elif token=='(':
            left=self.expression(); self.take(')')
        elif token.startswith('@'): left=dict(op='jdbc_parameter',slot=int(token[1:]))
        elif token.startswith("'"): left=dict(op='literal',sql=token)
        elif token.isdigit() or token in ('null','true','false'): left=dict(op='literal',sql=token)
        elif IDENT.fullmatch(token) and token not in CLAUSES and token not in PRECEDENCE:
            if self.peek()=='.': self.take(); token+='.'+self.name()
            if self.peek()=='(':
                self.take(); arguments=[]; distinct=False
                if self.peek()=='distinct':self.take();distinct=True
                if self.peek()!=')':
                    while True:
                        if self.peek()=='*': self.take(); arguments.append(dict(op='wildcard'))
                        else: arguments.append(self.expression())
                        if self.peek()!=',': break
                        self.take()
                self.take(')')
                left=dict(op='sql_call',name=token,arguments=arguments,distinct=distinct,semantics='unresolved')
            else: left=dict(op='column_reference',name=token)
        else: raise ValueError('unsupported expression token '+repr(token))
        while self.peek() in PRECEDENCE and PRECEDENCE[self.peek()]>=minimum:
            operator=self.take(); precedence=PRECEDENCE[operator]
            if operator=='is' and self.peek()=='not': self.take(); operator='is not'
            left=dict(op='binary',operator=operator,left=left,right=self.expression(precedence+1))
        return left

    def parse(self):
        kind=self.take(); assignments=[]; projections=[]; predicate=None; ordering=[]; limit=None; locking=None
        if kind=='insert':
            self.take('into');table=self.qualified();self.take('(');columns=[]
            while True:
                columns.append(self.name())
                if self.peek()!=',': break
                self.take()
            self.take(')');self.take('values');self.take('(');values=[]
            while True:
                values.append(self.expression())
                if self.peek()!=',': break
                self.take()
            self.take(')')
            if len(values)!=len(columns) or len(set(columns))!=len(columns):raise ValueError('column/value arity or duplicate target')
            assignments=[dict(column=c,value=v) for c,v in zip(columns,values)]
        elif kind=='update':
            table=self.qualified();self.take('set')
            while True:
                column=self.name();self.take('=');assignments.append(dict(column=column,value=self.expression()))
                if self.peek()!=',': break
                self.take()
            if len({x['column'] for x in assignments})!=len(assignments):raise ValueError('duplicate update target')
        elif kind=='select':
            while True:
                value=self.expression();alias=None
                if self.peek()=='as':self.take();alias=self.name()
                projections.append(dict(position=len(projections)+1,alias=alias,value=value))
                if self.peek()!=',':break
                self.take()
            self.take('from');table=self.qualified()
        else: raise ValueError('unsupported statement kind '+kind)
        if kind in ('select','update') and self.peek()=='where':self.take();predicate=self.expression()
        if kind=='select' and self.peek()=='order':
            self.take();self.take('by')
            while True:
                value=self.expression();direction=self.take() if self.peek() in ('asc','desc') else 'asc'
                ordering.append(dict(value=value,direction=direction))
                if self.peek()!=',':break
                self.take()
        if kind=='select' and self.peek()=='limit':self.take();limit=self.expression()
        if kind=='select' and self.peek()=='for':self.take();self.take('update');locking='for update'
        if self.peek():raise ValueError('unsupported trailing clause '+self.peek())
        return dict(kind=kind,table=table,assignments=assignments,projections=projections,
                    predicate=predicate,ordering=ordering,limit=limit,locking=locking,
                    parameter_count=self.parameters,semantics='declared structure only; types, calls, errors and implicit effects unresolved')


def parse_sql(sql):
    try:return dict(status='represented',ir=Parser(sql).parse())
    except (ValueError,IndexError,RecursionError) as error:return dict(status='unknown',reason=str(error),ir=None)


def expressions(ir):
    for assignment in ir['assignments']:
        yield 'write_value',dict(table=ir['table'],column=assignment['column']),assignment['value']
    for projection in ir['projections']:
        yield 'projection',dict(table=ir['table'],position=projection['position']),projection['value']
    if ir['predicate'] is not None:yield 'row_selection',dict(table=ir['table']),ir['predicate']
    for index,order in enumerate(ir['ordering']):
        yield 'ordering',dict(table=ir['table'],position=index+1,direction=order['direction']),order['value']
    if ir['limit'] is not None:yield 'row_limit',dict(table=ir['table']),ir['limit']


def walk(value):
    if isinstance(value,dict):
        if 'op' in value:yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)


def recover(facts):
    nodes=[];edges=[];statements=[];paths=[];unknowns=[]
    def node(kind,**kw):
        ident='n'+str(len(nodes));nodes.append(dict(id=ident,kind=kind,**kw));return ident
    def edge(a,b,kind):edges.append(dict(**{'from':a,'to':b},kind=kind))
    def expression_tree(term, parameter_nodes):
        current=node('sql_value',term={k:v for k,v in term.items() if not isinstance(v,(dict,list))})
        if term['op']=='jdbc_parameter':parameter_nodes.setdefault(term['slot'],[]).append(current)
        for key,value in term.items():
            if isinstance(value,dict) and 'op' in value:edge(expression_tree(value,parameter_nodes),current,key)
            elif isinstance(value,list):
                for i,v in enumerate(value):
                    if isinstance(v,dict) and 'op' in v:edge(expression_tree(v,parameter_nodes),current,key+':'+str(i))
        return current
    for spec in facts['statements']:
        scoped=dict(bindings=[b for b in facts['bindings'] if b['receiver']==spec['receiver']])
        if spec['status']!='finite_sql_set' or not spec['receiver_stable']:
            unknowns.append(dict(receiver=spec['receiver'],reason='unresolved SQL factory'));continue
        for sql in spec['sql_alternatives']:
            parsed=parse_sql(sql);record=dict(receiver=spec['receiver'],source=spec,sql=sql,**parsed);statements.append(record)
            if parsed['status']=='unknown':unknowns.append(dict(receiver=spec['receiver'],sql=sql,reason=parsed['reason']));continue
            ir=parsed['ir'];action=node('sql_action',statement_kind=ir['kind'],table=ir['table'],locking=ir['locking'],source=spec)
            ports=[]
            if len(spec['sql_alternatives'])>1:edge(node('unknown_control',reason='alternative SQL targets'),action,'control')
            for role,destination,term in expressions(ir):
                sink=node('effect_port',role=role,destination=destination)
                ports.append((role,sink))
                parameter_nodes={}
                edge(action,sink,'declares');expression_root=expression_tree(term,parameter_nodes);edge(expression_root,sink,'value')
                for slot in sorted({t['slot'] for t in walk(term) if t['op']=='jdbc_parameter'}):
                    hits=[b for b in scoped['bindings'] if b['operation'].startswith('set') and b['arguments'] and b['arguments'][0]==dict(op='literal',value=slot)]
                    inspected=inspect_binding(scoped,hits[0]['owner'],slot) if len(hits)==1 else dict(status='unknown',reason='missing_or_multiple_bindings')
                    value=inspected.get('value');source_nodes=[]
                    if inspected['status']=='extracted':
                        source=node('java_value',term=value);binding=node('jdbc_binding',slot=slot,setter=inspected['setter'],receiver=spec['receiver'])
                        edge(source,binding,'value')
                        for leaf in parameter_nodes[slot]:edge(binding,leaf,'parameter_value')
                        source_nodes=[source,binding]
                    else:
                        opaque=node('unknown_value',slot=slot,details=inspected)
                        for leaf in parameter_nodes[slot]:edge(opaque,leaf,'parameter_value')
                        source_nodes=[opaque]
                        unknowns.append(dict(receiver=spec['receiver'],slot=slot,role=role,reason=inspected['reason']))
                    paths.append(dict(receiver=spec['receiver'],slot=slot,role=role,destination=destination,
                                      sql_expression=term,java_value=value,binding_status=inspected['status'],
                                      source_nodes=source_nodes,expression_root=expression_root,effect_node=sink,
                                      sql_kind=ir['kind'],equivalence_proved=False))
            for role,selector in ports:
                if role not in ('row_selection','ordering','row_limit'):continue
                for output_role,output in ports:
                    if output_role=='projection' or output_role=='write_value' and role=='row_selection':
                        edge(selector,output,'row_control' if role=='row_selection' else role)
    return dict(kind='value-effect-ir/v1',statements=statements,nodes=nodes,edges=edges,paths=paths,
                unknowns=unknowns,dag=condense(nodes,edges),whole_program_complete=False,
                risk_score=None,automatic_deletion_authorized=False,
                limits=['single-table explicit SQL syntax only; SQL operators/calls are syntax, not evaluated semantics',
                        'Java parameter values stop at method boundary; no interprocedural or heap completeness',
                        'SQL projections are not proven Java/application observations; result decoding unmodeled',
                        'predicates/order/limits retained; row cardinality, schema, triggers and concurrency unresolved'])
