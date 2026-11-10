#!/usr/bin/env python3
"""Development controls; no held-out module source or runtime labels."""
import copy
import json
from value_effect_ir import parse_sql, recover, expressions, walk


def controls():
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append(name)
    def parsed(sql):
        result=parse_sql(sql);assert result['status']=='represented',result
        return result['ir']
    def links(ir):
        return [(role,destination,t['slot']) for role,destination,term in expressions(ir)
                for t in walk(term) if t['op']=='jdbc_parameter']
    a=parsed('INSERT INTO ledger (stamp, amount) VALUES (?, ?);')
    b=parsed('UPDATE ledger SET stamp=?, amount=? WHERE id=?;')
    check('INSERT and UPDATE share assignment representation',a['assignments']==b['assignments'])
    check('WHERE parameter is selection, not a written value',links(b)[-1]==('row_selection',{'table':'ledger'},3))
    c=parsed('UPDATE ledger SET amount=amount+? WHERE id=? AND active IS NOT NULL')
    check('prior column value and arithmetic retained',c['assignments'][0]['value']==dict(op='binary',operator='+',left=dict(op='column_reference',name='amount'),right=dict(op='jdbc_parameter',slot=1)))
    d=parsed('SELECT sum(amount) AS total, id FROM ledger WHERE id>=? AND id<? ORDER BY id DESC LIMIT 1 FOR UPDATE')
    check('projection, ordering, limit and lock retained',len(d['projections'])==2 and d['ordering'][0]['direction']=='desc' and d['limit']==dict(op='literal',sql='1') and d['locking']=='for update')
    check('SQL call semantics remain unresolved',d['projections'][0]['value']['semantics']=='unresolved')
    check('parameter slots span projection and WHERE',links(parsed('SELECT ? + amount FROM ledger WHERE id=?'))==[('projection',{'table':'ledger','position':1},1),('row_selection',{'table':'ledger'},2)])
    check('comments and string question marks do not shift slots',parsed("INSERT INTO ledger (stamp,amount) VALUES ('?', ?) -- ?\n")['parameter_count']==1)
    renamed=parsed('UPDATE other SET payload=?, number=? WHERE key=?')
    check('same roles survive identifiers renamed',[r for r,_,_ in links(renamed)]==[r for r,_,_ in links(b)])
    e=parsed('SELECT count(DISTINCT item) FROM ledger WHERE id=? OR id=? AND active=1')
    check('AND binds tighter than OR',e['predicate']['operator']=='or' and e['predicate']['right']['operator']=='and')
    check('aggregate DISTINCT preserved',e['projections'][0]['value']['distinct'])
    for sql in ['UPDATE ledger SET a=?; DELETE FROM ledger',
                'UPDATE ledger SET a=? RETURNING a', 'INSERT INTO ledger (a) VALUES (?) ON CONFLICT DO NOTHING',
                'SELECT a FROM ledger JOIN other ON ledger.id=other.id',
                'SELECT (SELECT a FROM other) FROM ledger', 'INSERT INTO ledger (a,a) VALUES (?,?)',
                'UPDATE ledger SET a=?,a=?', 'SELECT * FROM ledger',
                'SELECT a FROM ledger GROUP BY a', 'DELETE FROM ledger WHERE id=?']:
        check('unsupported whole statement retained: '+sql,parse_sql(sql)['status']=='unknown')
    def call(operation,args,offset):
        return dict(owner='anonymous#write(int)',operation=operation,arguments=args,offset=offset,
                    receiver='statement@0',receiver_stable=True,guards=[],target='jdbc#'+operation,file='control.java',line=1)
    facts=dict(statements=[dict(receiver='statement@0',receiver_stable=True,status='finite_sql_set',
        sql_alternatives=['UPDATE ledger SET stamp=? WHERE id=?'],guard_constraints=[])],
        bindings=[call('setTimestamp',[dict(op='literal',value=1),dict(op='timestamp',value=dict(op='parameter',position=0,type='long'))],1),
                  call('setInt',[dict(op='literal',value=2),dict(op='parameter',position=1,type='int')],2),call('executeUpdate',[],3)])
    g=recover(facts)
    check('all setter types linked without time root selection',len(g['paths'])==2 and all(p['binding_status']=='extracted' for p in g['paths']))
    adjacency={n['id']:set() for n in g['nodes']}
    for edge in g['edges']:adjacency[edge['from']].add(edge['to'])
    def reachable(start,target):
        todo=[start];seen=set()
        while todo:
            n=todo.pop()
            if n==target:return True
            if n not in seen:seen.add(n);todo.extend(adjacency[n])
        return False
    check('Java source reaches actual SQL expression and effect',all(reachable(p['source_nodes'][0],p['expression_root']) and reachable(p['expression_root'],p['effect_node']) for p in g['paths']))
    check('WHERE source controls write without becoming written payload',reachable(g['paths'][1]['source_nodes'][0],g['paths'][0]['effect_node']) and not reachable(g['paths'][1]['source_nodes'][0],g['paths'][0]['expression_root']))
    altered=copy.deepcopy(facts);altered['bindings'][0]['arguments'][1]=dict(op='unknown',reason='external call')
    u=recover(altered)
    check('unknown Java term keeps SQL destination but no known value',u['paths'][0]['binding_status']=='unknown' and u['paths'][0]['java_value'] is None and u['paths'][0]['destination']['column']=='stamp')
    dynamic=copy.deepcopy(facts);dynamic['bindings'][0]['arguments'][0]=dict(op='unknown',reason='dynamic')
    check('dynamic slot not assigned to a guessed destination',recover(dynamic)['paths'][0]['binding_status']=='unknown')
    duplicate=copy.deepcopy(facts);duplicate['bindings'].append(copy.deepcopy(facts['bindings'][0]))
    check('duplicate setter remains unknown',recover(duplicate)['paths'][0]['binding_status']=='unknown')
    check('representation never becomes a safety score',g['risk_score'] is None and not g['automatic_deletion_authorized'] and not g['whole_program_complete'])
    return dict(checks=checks,count=len(checks))


if __name__=='__main__':print(json.dumps(controls(),indent=2))
