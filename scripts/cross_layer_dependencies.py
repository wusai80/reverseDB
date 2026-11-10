"""Restricted may-dependency recovery; no generator maps or business names.

Java AST -> JDBC position -> SQL function formal -> explicit INSERT column.
Unsupported SQL/control/alias facts remain visible, never deletion permission.
"""
import re
from jdbc_boundary_contracts import inspect_binding

IDENT=r'[a-z_][a-z_0-9]*'
TOKEN=re.compile(r"\s+|--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|::|:=|[\[\](),.?;=+*<>|\-]",re.S)


def tokens(text):
    out=[]; pos=0
    while pos<len(text):
        m=TOKEN.match(text,pos)
        if not m: raise ValueError('unsupported SQL token at '+str(pos))
        t=m.group(); pos=m.end()
        if t.isspace() or t.startswith(('--','/*')): continue
        out.append(t if t.startswith("'") else t.lower())
    return out


def group(t,start):
    if t[start]!='(': raise ValueError('expected argument list')
    parts=[]; current=[]; depth=0
    for i in range(start+1,len(t)):
        v=t[i]
        if v==')' and depth==0: return parts+[current],i+1
        if v==',' and depth==0: parts.append(current); current=[]; continue
        depth+=int(v=='(')-int(v==')'); current.append(v)
    raise ValueError('unclosed argument list')


def qualified(t,start):
    if not re.fullmatch(IDENT,t[start]): raise ValueError('unknown identifier')
    end=start+1
    if end<len(t) and t[end]=='.':
        if end+1>=len(t) or not re.fullmatch(IDENT,t[end+1]): raise ValueError('unknown qualification')
        return t[start]+'.'+t[end+1],end+2
    return t[start],end


def insert(t,start=0):
    if t[start:start+2]!=['insert','into']: raise ValueError('not explicit INSERT')
    table,i=qualified(t,start+2); columns,i=group(t,i)
    if any(len(c)!=1 or not re.fullmatch(IDENT,c[0]) for c in columns): raise ValueError('unknown target columns')
    if t[i]!='values': raise ValueError('INSERT SELECT unsupported')
    values,end=group(t,i+1)
    if len(values)!=len(columns): raise ValueError('column/value arity mismatch')
    if end<len(t) and t[end]!=';': raise ValueError('multirow/conflict/returning INSERT unsupported')
    return dict(table=table,columns=[c[0] for c in columns],values=values,end=end)


def functions(ddl):
    # Bodies remain may-execute summaries; lexical IF/exception behavior is not proved.
    pattern=re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([\w.]+)\s*\((.*?)\)\s*RETURNS\b.*?\bAS\s+(\$\w*\$)(.*?)\3',re.I|re.S)
    # Locate declarations only at SQL top level. Text inside comments, strings
    # or another dollar-quoted function body cannot introduce a function.
    opaque=re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|(\$\w*\$)",re.S)
    masked=list(ddl); cursor=0
    while match:=opaque.search(ddl,cursor):
        end=match.end()
        if match[1]:
            closing=ddl.find(match[1],end)
            end=len(ddl) if closing<0 else closing+len(match[1])
        for i in range(match.start(),end):
            if masked[i]!='\n': masked[i]=' '
        cursor=end
    result={}
    for declaration in re.finditer(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b',''.join(masked),re.I):
        m=pattern.match(ddl,declaration.start())
        if not m: continue  # Caller reports missing/unsupported target, never guesses.
        item=dict(name=m[1].lower(),line=ddl.count('\n',0,m.start())+1,body=m[4],unknowns=[])
        try:
            params,_=group(tokens('('+m[2]+')'),0)
            if any(len(p)<2 or not re.fullmatch(IDENT,p[0]) or ' '.join(p[1:]) not in
                   {'integer','numeric','double precision','text','boolean','timestamp','jsonb'} for p in params): raise ValueError('unsupported function signature')
            item['parameters']=[dict(name=p[0],type=' '.join(p[1:])) for p in params]
            if len({p[0] for p in params})!=len(params): raise ValueError('duplicate parameter')
            t=tokens(m[4]); names={p[0] for p in params}
            if any(v in names and i+1<len(t) and t[i+1] in (':=','=') for i,v in enumerate(t)):
                # '=' may also be a predicate: conservatively unknown rather than
                # confusing a compared parameter with a reassigned parameter.
                raise ValueError('parameter assignment/comparison needs richer analysis')
            if any(t[i]=='into' and t[i+1] in names for i in range(len(t)-1)) or 'execute' in t or 'call' in t:
                raise ValueError('parameter destination or dynamic/call effect unsupported')
            declaration=t[:t.index('begin')] if 'begin' in t else []
            if any(v in names and i+1<len(declaration) and declaration[i+1] in
                   {'timestamp','integer','text','numeric','boolean','double','jsonb'} for i,v in enumerate(declaration)):
                raise ValueError('possible parameter shadowing')
            item['inserts']=[insert(t,i) for i in range(len(t)) if t[i:i+2]==['insert','into']]
            item['control_unknown']=bool(set(t).intersection({'if','raise','loop','exception'}))
        except (ValueError,IndexError) as e: item['unknowns'].append(str(e))
        result.setdefault(item['name'],[]).append(item)
    return result


def condense(nodes,edges):
    """Tarjan SCCs, then a DAG retaining membership and all original edges."""
    adjacency={n['id']:[] for n in nodes}
    for e in edges: adjacency[e['from']].append(e['to'])
    indices={}; low={}; stack=[]; active=set(); components=[]
    def visit(v):
        indices[v]=low[v]=len(indices); stack.append(v); active.add(v)
        for w in adjacency[v]:
            if w not in indices: visit(w); low[v]=min(low[v],low[w])
            elif w in active: low[v]=min(low[v],indices[w])
        if low[v]==indices[v]:
            part=[]
            while True:
                w=stack.pop(); active.remove(w); part.append(w)
                if w==v: break
            components.append(sorted(part))
    for v in adjacency:
        if v not in indices: visit(v)
    membership={v:i for i,part in enumerate(components) for v in part}
    pairs=sorted({(membership[e['from']],membership[e['to']]) for e in edges if membership[e['from']]!=membership[e['to']]})
    return dict(components=[dict(id=i,members=part,cyclic=len(part)>1 or any(e['from']==e['to']==part[0] for e in edges)) for i,part in enumerate(components)],
                edges=[dict(source=a,target=b) for a,b in pairs])


def recover(facts,ddl=''):
    nodes=[]; edges=[]; paths=[]; unknowns=[]; catalog=functions(ddl)
    def node(kind,**data):
        id='n'+str(len(nodes)); nodes.append(dict(id=id,kind=kind,**data)); return id
    def edge(a,b,kind): edges.append(dict(**{'from':a,'to':b},kind=kind))
    def expression(value):
        root=node('java_value',term=value)
        for child in value.values():
            if isinstance(child,dict) and 'op' in child: edge(expression(child),root,'data')
        return root
    specs={s['receiver']:s for s in facts.get('statements',[])}
    selected=[b for b in facts['bindings'] if b['operation']=='setTimestamp' and len(b['arguments'])>=2]
    for b in selected:
        slot=b['arguments'][0].get('value'); spec=specs.get(b['receiver'])
        if not isinstance(slot,int): unknowns.append(dict(binding=b,reason='dynamic JDBC index')); continue
        # inspect_binding is conservative if a method has several statement sites
        # sharing one index; isolate this statically identified statement object.
        scoped=dict(bindings=[x for x in facts['bindings'] if x['receiver']==b['receiver']])
        inspected=inspect_binding(scoped,b['owner'],slot)
        if inspected['status']!='extracted' or not spec or spec['status']!='finite_sql_set' or not spec['receiver_stable']:
            unknowns.append(dict(binding=b,reason='binding or SQL factory unknown',details=inspected))
            opaque=node('unknown_dependency',reason='binding or SQL factory unknown',source=b)
            edge(expression(b['arguments'][1]),opaque,'unknown'); continue
        value=expression(inspected['value']); bind=node('jdbc_binding',slot=slot,setter=b['target'],source=dict(file=b['file'],line=b['line']))
        edge(value,bind,'data')
        for sql in spec['sql_alternatives']:
            sqlnode=node('sql_statement',sql=sql,source=spec); edge(bind,sqlnode,'binds')
            for guard in spec.get('guard_constraints',[]):
                control=node('finite_target_guard',evidence=guard); edge(control,sqlnode,'control')
            if len(spec['sql_alternatives'])>1:
                control=node('unresolved_dispatch',reason=spec['choice_control']); edge(control,sqlnode,'control')
            try:
                t=tokens(sql)
                if t[:2]==['insert','into']:
                    ins=insert(t); ordinal=0; matched=False
                    for column,v in zip(ins['columns'],ins['values']):
                        count=v.count('?')
                        if ordinal<slot<=ordinal+count:
                            if v!=['?']: raise ValueError('transformed INSERT expression unsupported')
                            sink=node('database_column',table=ins['table'],column=column); edge(sqlnode,sink,'writes')
                            paths.append(dict(owner=b['owner'],jdbc_slot=slot,table=ins['table'],column=column,value=inspected['value'],
                              route=[value,bind,sqlnode,sink],function=None,formal=None,source=b,sql_field_premise=spec.get('sql_field_premise'))); matched=True
                        ordinal+=count
                    if not matched: raise ValueError('JDBC slot has no INSERT value')
                elif t and t[0]=='select':
                    name,i=qualified(t,1); args,end=group(t,i)
                    if end<len(t) and t[end:]!=[';']: raise ValueError('unsupported SELECT call shape')
                    targets=catalog.get(name,[])
                    if len(targets)!=1: raise ValueError('missing or overloaded function')
                    f=targets[0]
                    if f['unknowns']: raise ValueError('; '.join(f['unknowns']))
                    if len(args)!=len(f['parameters']): raise ValueError('function arity mismatch')
                    ordinal=0; formal=None; position=None
                    for j,a in enumerate(args):
                        count=a.count('?')
                        if ordinal<slot<=ordinal+count:
                            if a!=['?'] and a!=['?','::',f['parameters'][j]['type']]: raise ValueError('unsupported argument transformation')
                            formal=f['parameters'][j]['name']; position=j+1
                        ordinal+=count
                    if formal is None: raise ValueError('unmapped JDBC slot')
                    param=node('function_parameter',function=name,position=position,name=formal,type=f['parameters'][position-1]['type'],line=f['line'])
                    edge(sqlnode,param,'argument')
                    matched=False
                    for ins in f['inserts']:
                        for column,v in zip(ins['columns'],ins['values']):
                            if formal not in v: continue
                            if v!=[formal]: raise ValueError('transformed function INSERT value unsupported')
                            sink=node('database_column',table=ins['table'],column=column); edge(param,sink,'writes')
                            if f['control_unknown']:
                                control=node('unresolved_procedure_control',function=name); edge(control,sink,'control')
                            paths.append(dict(owner=b['owner'],jdbc_slot=slot,table=ins['table'],column=column,value=inspected['value'],
                              route=[value,bind,sqlnode,param,sink],function=name,formal=formal,formal_position=position,source=b,
                              control='may-write; procedure guards/exceptions unresolved' if f['control_unknown'] else 'no lexical control construct detected')); matched=True
                    if not matched: raise ValueError('formal has no supported INSERT sink')
                else: raise ValueError('unsupported SQL shape')
            except (ValueError,IndexError) as error:
                reason=str(error); opaque=node('unknown_dependency',reason=reason); edge(sqlnode,opaque,'unknown'); unknowns.append(dict(binding=b,sql=sql,reason=reason))
    return dict(kind='cross-layer-may-dependencies/v1',nodes=nodes,edges=edges,paths=paths,unknowns=unknowns,
      dag=condense(nodes,edges),automatic_deletion_authorized=False,
      limits='Selected timestamp-binding slices only; initializer-based SQLStmt with API summaries; SQL may-write lineage, not path feasibility or whole-program effects')


def correspond(original,candidate):
    """Infer structural matches by sink identity, retaining one-to-many targets."""
    result=[]
    for p in original['paths']:
        matches=[q for q in candidate['paths'] if (q['table'],q['column'])==(p['table'],p['column'])]
        result.append(dict(sink=p['table']+'.'+p['column'],original_slot=p['jdbc_slot'],original_owner=p['owner'],
          candidates=[dict(owner=q['owner'],jdbc_slot=q['jdbc_slot'],function=q['function'],formal=q['formal'],route=q['route']) for q in matches],
          status='structural_candidates' if matches else 'unresolved',equivalence_proved=False))
    return result
