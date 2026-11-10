"""Non-authorizing SQL access observations behind a common description.

The legacy adapter preserves the first project's syntax summary. The SQLite
adapter uses compile-time authorizer events in a NEW read-only connection.
Neither proves purity, equivalent results, runtime reads, or safe deletion.
No project names or application-query patterns are used.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from fusion_regions import sql_effect


def base(sql, adapter):
    return dict(schema='sql-access-observation/v1',adapter=adapter,sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
        direct_access=None,functions=[],complete_effects=False,authorizes_replacement=False,
        unresolved=['Result/order/type/error equivalence not established',
                    'Connection callbacks, collation behavior, runtime locks and visibility not proved',
                    'Observation is not a deployment or lowering contract'])


def legacy_observation(sql):
    result=base(sql,'unchanged-first-project-syntax')
    effect=sql_effect(sql)
    result.update(status='syntax_summary' if effect['known'] else 'unknown',legacy_effect=effect)
    if effect['known']:
        result['direct_access']={k:effect[k] for k in ['reads','writes','locks']}
    return result


def sqlite_observation(sql, database, parameters=(), *, test_functions=()):
    """Compile a supplied SELECT/WITH without executing its business VM.

    For controlled source analysis, not an arbitrary hostile-SQL sandbox.
    Fresh connection: no application authorizer/collation/progress callbacks.
    EXPLAIN is not used on PRAGMA/DDL. Existing virtual-table schemas are
    conservatively declined before compiling the supplied SQL. Eponymous
    modules may still be recognized during prepare; out-of-catalog reads stay
    unknown. EXPLAIN opcodes are retained only as version-pinned diagnostics.
    test_functions permits controlled negative witnesses, never purity claims.
    """
    result=base(sql,'sqlite-compile-authorizer')
    leading=re.sub(r'\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*','',sql,flags=re.S)
    if not re.match(r'(SELECT|WITH)\b',leading,re.I):
        result.update(status='unsupported_statement',reason='Only SELECT/WITH compilation is analyzed')
        return result
    path=Path(database).resolve()
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,cached_statements=0)
    events=[]
    try:
        conn.enable_load_extension(False)
        schema=conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        table_list=conn.execute('PRAGMA table_list').fetchall()
        result.update(sqlite_version=sqlite3.sqlite_version,
            schema_sha256=hashlib.sha256(json.dumps(schema,ensure_ascii=False).encode()).hexdigest(),
            deployment='Dedicated read-only SQLite connection; not an attestation of application connection hooks')
        if any(row[2] in ['virtual','shadow'] for row in table_list):
            result.update(status='unsupported_schema',reason='Virtual/shadow table planning effects not covered')
            return result
        for name,nargs,fn in test_functions:conn.create_function(name,nargs,fn)
        function_catalog=conn.execute('PRAGMA function_list').fetchall()
        relation_kinds={r[1]:r[0] for r in schema if r[0] in ['table','view']}
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        def authorize(action,arg1,arg2,db,source):
            events.append(dict(action=action,arg1=arg1,arg2=arg2,database=db,source=source))
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        conn.set_authorizer(authorize)
        try:
            plan=conn.execute('EXPLAIN '+sql,parameters).fetchall()
        except sqlite3.Error as exc:
            result.update(status='compile_error',error=dict(type=type(exc).__name__,message=str(exc)))
            return result
        finally:
            conn.set_authorizer(None)
        reads=[e for e in events if e['action']==sqlite3.SQLITE_READ]
        names=sorted({e['arg1'] for e in reads})
        unknown=[e for e in reads if e['database']!='main' or e['arg1'] not in relation_kinds]
        functions=sorted({e['arg2'] for e in events if e['action']==sqlite3.SQLITE_FUNCTION})
        result.update(status='compiled_access_observation' if not unknown else 'unknown_relation_access',
            direct_access=dict(reads=names,writes=[],locks=None),
            relation_details=[dict(name=n,kind=relation_kinds.get(n,'unknown'),
                columns=sorted({e['arg2'] for e in reads if e['arg1']==n and e['arg2'] is not None})) for n in names],
            functions=[dict(name=n,registered_variants=[list(f) for f in function_catalog if f[0].lower()==n.lower()],
                            runtime_effects='unknown, including functions marked builtin') for n in functions],
            unknown_relations=unknown,diagnostic_vm=plan,
            interpretation='Possible direct accesses requested during compilation; not dynamic access counts or all effects',
            application_rows_fetched=False)
        return result
    finally:
        result['authorizer_events']=events
        conn.close()


def attach_observations(description, observations):
    """Check exact per-unit SQL coverage. Leave source/effect/body/backend facts intact."""
    import copy
    units=description['program']['units']
    if set(observations)!={n['id'] for n in units}:
        raise ValueError('Unit observation coverage differs from source description')
    for n in units:
        expected=[hashlib.sha256(q.encode()).hexdigest() for q in n.get('queries',[])]
        supplied=[o['sql_sha256'] for o in observations[n['id']]]
        if supplied!=expected:raise ValueError('Observation SQL/order mismatch: '+n['id'])
        if any(o.get('authorizes_replacement') is not False or o.get('complete_effects') is not False for o in observations[n['id']]):
            raise ValueError('Observation cannot declare semantic admission')
    result=copy.deepcopy(description)
    result['analysis_observations']=dict(sql_access=copy.deepcopy(observations),authorizes_replacement=False)
    return result
