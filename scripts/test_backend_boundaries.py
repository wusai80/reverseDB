#!/usr/bin/env python3
"""Fixed common boundary description plus real SQLite codec witnesses."""
import copy
from contextlib import ExitStack
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch
from uuid import uuid4

from backend_boundaries import describe_boundary, positional_parameters, decode_first_field
from region_contracts import fingerprint, select_existing_binding
from test_beets_feasibility import snapshot

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'work/beets/source-e978874d'
COMMON=ROOT/'evidence/sql-effect-observations/20261109-9f2591af957b'
BASE=ROOT/'evidence/beets-feasibility/20261108-a03fafe1c20f'
SCALAR=ROOT/'evidence/beets-scalar-contract/20261108-0671127c5421'
FIRST=ROOT/'evidence/fusion-placement/20261105-ea883b4b7dce'
JAVA={
    'neworder':ROOT/'evidence/neworder-fusion/20261105-4c76a7feb013/generated/FusedNewOrder.java',
    'payment':ROOT/'evidence/payment-segments/20261105-707221611457/generated/SegmentedPayment.java',
}
VALUES=[('zero',0),('metadata-not-count',17),('negative',-3),('null',None),
        ('half-even-low',2.5),('half-even-high',3.5),('negative-half',-2.5),
        ('wide-int',2**53+1),('max-signed',2**63-1),('min-signed',-2**63),
        ('numeric-text','17.6'),('nonnumeric-text','not-a-number'),
        ('numeric-blob',b'17.6'),('invalid-blob',b'\xff'),
        ('positive-infinity',float('inf')),('negative-infinity',float('-inf')),
        ('nan-storage',float('nan')),('empty',None)]
PARAMETERS=[1,0,-1,2**63-1,2**63,None,'1',b'1',1.0]


def value_repr(value):
    if isinstance(value,bytes):return dict(type='bytes',hex=value.hex())
    if isinstance(value,float) and not math.isfinite(value):return dict(type='float',value=repr(value))
    return dict(type=type(value).__name__,value=value)


def main():
    out=ROOT/'evidence/backend-boundaries'/('20261109-'+uuid4().hex[:12]);out.mkdir(parents=True)
    def load(p):return json.loads(p.read_text())
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    checks=[]
    def check(n,v):
        assert v,n
        checks.append(n)
    def ref(p):return dict(path=str(p.relative_to(ROOT)),sha256=fingerprint(p))
    def outcome(fn):
        try:return dict(result=value_repr(fn()))
        except Exception as exc:return dict(error=type(exc).__name__,message=str(exc))
    save('protocol.json',dict(descriptions=['NewOrder','Payment','beets'],
        decode_cases=[dict(name=n,input=value_repr(v)) for n,v in VALUES],
        parameter_cases=[value_repr(v) for v in PARAMETERS],
        lifecycle_cases=['no outer context','existing outer context'],
        negative_cases=['field-conversion-effect','field-conversion-error','all-conversion-effect',
                        'all-conversion-error','getter-override','mtime-getter-error'],
        scope='Codec and resource boundary only; original full row/flex reads retained when constructing packets; no new read-deletion admission',
        no_performance=True,no_production_change=True,no_sql_projection_or_limit=True,
        stop='One fixed local boundary experiment; no performance tuning or new optimizer'))
    for f in ['backend_boundaries.py','test_backend_boundaries.py']:
        shutil.copy2(ROOT/'scripts'/f,out/f)
    try:
        check('common inputs intact',all(fingerprint(COMMON/p)==h for p,h in load(COMMON/'manifest.json').items()))
        check('whole pinned beets source intact',all(fingerprint(SOURCE/p)==h for p,h in load(BASE/'source-manifest.json').items()))
        for f in ['fusion_placement.py','fusion_regions.py','fusion_placement_contracts.py']:
            check(f+' unchanged',fingerprint(ROOT/'scripts'/f)==fingerprint(FIRST/'inputs/scripts'/f))
        descriptions={};boundaries={}
        for name in ['neworder','payment','beets']:
            d=load(COMMON/(name+'-description.json'));descriptions[name]=d;before=copy.deepcopy(d)
            if name!='beets':
                archive=JAVA[name].parent.parent
                check(name+' actual generated bridge hash',fingerprint(JAVA[name])==load(archive/'manifest.json')[str(JAVA[name].relative_to(archive))])
                text=JAVA[name].read_text()
                check(name+' resident connection in actual bridge',('execute(Connection connection,' in text or 'execute(Connection c,' in text) and '.prepareStatement(' in text)
                roles={v:dict(role='parameter',type=t,binding='Inherited source-bound JDBC ABI '+t) for v,t in d['scope']['interface_inputs'].items()}
                resources={'connection':dict(type='jdbc_connection',owner='host coordinator',lifetime='existing outer transaction',serialized=False)}
                decoders={v:dict(type=t,transport_shape='Existing PostgreSQL/JDBC result representation',host_decode='Retain original generated bridge and catalog '+t) for v,t in d['scope']['interface_outputs'].items()}
                evidence=[ref(JAVA[name]),ref(FIRST/(name+'-contract.json'))]
            else:
                roles=dict(album_id=dict(role='parameter',type='python_int',binding='Original MatchQuery parameter passed unchanged to sqlite3'),
                    db=dict(role='resident_resource',type='sqlite_library',owner='existing host Library/thread connection',lifetime='original caller context; do not replace connection or transaction',serialized=False),
                    sort=dict(role='host_configuration',type='beets_sort',owner='original Library sort provider',lifetime='read per invocation at original point; no new cache',serialized=False))
                resources={}
                decoders={'tracktotal':dict(type='python_int',transport_shape='Complete ordered sqlite3.Row list from unchanged first query',
                    host_decode='First row fixed field then original Item._convert; preserve empty/error behavior and context position')}
                evidence=[ref(SOURCE/'beets/dbcore/db.py'),ref(SOURCE/'beets/library/library.py'),ref(SOURCE/'beets/library/models.py')]
            b=describe_boundary(d,input_roles=roles,ambient_resources=resources,output_decoders=decoders,evidence=evidence)
            boundaries[name]=b;save(name+'-boundary.json',b)
            check(name+' source interface remains intact',d==before)
            check(name+' original selection/unbound result unchanged',select_existing_binding(d,ROOT)==load(COMMON/(name+'-decision.json')))
            check(name+' role description grants no backend',not b['authorizes_lowering'] and not b['changes_selector_input'])
        check('beets only album id is a positional parameter',boundaries['beets']['parameter_values']=={'album_id':'python_int'})
        check('beets db and sort dependencies retained as environment',set(boundaries['beets']['retained_environment'])=={'db','sort'})
        # Role validation cannot make a missing resource or return value vanish.
        rejects=[];b=boundaries['beets'];d=descriptions['beets']
        for case in ['drop-resource','serialize-resource','drop-decoder','wrong-type']:
            roles=copy.deepcopy(b['input_roles']);dec=copy.deepcopy(b['output_decoders'])
            if case=='drop-resource':roles.pop('db')
            elif case=='serialize-resource':roles['db']['serialized']=True
            elif case=='drop-decoder':dec={}
            else:dec['tracktotal']['type']='int32'
            try:describe_boundary(d,input_roles=roles,ambient_resources={},output_decoders=dec,evidence=b['evidence'])
            except ValueError as exc:rejects.append(dict(case=case,error=str(exc)))
        check('all invalid boundary descriptions rejected',len(rejects)==4);save('role-negative-controls.json',rejects)

        (out/'config').mkdir();(out/'config/config.yaml').write_text('plugins: []\n')
        os.environ['BEETSDIR']=str(out/'config');sys.path.insert(0,str(SOURCE))
        import beets
        from beets import plugins
        from beets.dbcore import db as core,query
        from beets.library import Library,Item
        beets.config['plugins']=[];beets.config['per_disc_numbering']=False
        check('fixed native field dispatch and original constructor',not list(plugins.find_plugins()) and 'tracktotal' not in Item._getters() and Item.__init__ is core.Model.__init__)
        original_db_hash=fingerprint(BASE/'100/library.db')
        def clone(path):
            with sqlite3.connect(BASE/'100/library.db') as src,sqlite3.connect(path) as dst:src.backup(dst)
        def original_decode(rows,flex,lib):return core.Results(Item,list(rows),lib,list(flex))[0].tracktotal
        def decode(rows):return decode_first_field(rows,'tracktotal',lambda v:Item._convert('tracktotal',v))
        records=[]
        for name,value in VALUES:
            folder=out/name;folder.mkdir();path=folder/'library.db';clone(path)
            lib=Library(path,directory=BASE/'100/music')
            if name!='empty':
                with sqlite3.connect(path) as sql:sql.execute('UPDATE items SET tracktotal=? WHERE album_id=1',(value,))
            before=snapshot(path)
            packet=lib.items(query.MatchQuery('album_id',999 if name=='empty' else 1))
            rows=list(packet.rows);flex=list(packet.flex_rows)
            a=outcome(lambda:original_decode(rows,flex,lib));z=outcome(lambda:decode(rows))
            check(name+' original model vs direct decoder result/type/error',a==z)
            check(name+' complete ordered packet retained',len(rows)==(0 if name=='empty' else 9) and (not rows or len(rows[0].keys())==len(Item._fields)))
            check(name+' complete database state unchanged by decoding',snapshot(path)==before)
            records.append(dict(case=name,input=value_repr(value),stored_first=None if not rows else value_repr(rows[0]['tracktotal']),
                rows=len(rows),flex_rows=len(flex),original=a,candidate=z))
            lib._close()
        save('decode-cases.json',records)
        cases={r['case']:r for r in records}
        expected={'metadata-not-count':17,'half-even-low':2,'half-even-high':4,'negative-half':-2,
                  'wide-int':2**53,'max-signed':2**63,'numeric-text':18,'numeric-blob':18,
                  'nonnumeric-text':0,'invalid-blob':0,'nan-storage':0}
        check('actual original conversions independently asserted',all(cases[n]['original']=={'result':value_repr(v)} for n,v in expected.items()))
        check('infinite numeric conversions preserve OverflowError',all(cases[n]['original']['error']=='OverflowError' for n in ['positive-infinity','negative-infinity']))
        check('empty packet exact native error',cases['empty']['original']==dict(error='IndexError',message='list index out of range'))

        path=out/'binding-and-lifecycle.db';clone(path);lib=Library(path,directory=BASE/'100/music')
        connection=lib._connection();before=snapshot(path)
        sql=next(c for c in load(SCALAR/'cases.json') if c['case']=='default')['arms'][0]['queries'][0]['sql']
        parameter_records=[]
        for value in PARAMETERS:
            original_parameters=query.MatchQuery('album_id',value).clause()[1]
            encoded=positional_parameters(value)
            check('parameter '+repr(value)+' not coerced or serialized',encoded[0] is value and original_parameters[0] is value)
            def read(params):
                with lib.transaction() as tx:return [dict(r) for r in tx.query(sql,params)]
            def read_result(params):
                try:
                    result=read(params)
                    return dict(rows=len(result),ids=[r['id'] for r in result])
                except Exception as exc:return dict(error=type(exc).__name__,message=str(exc))
            a,z=read_result(original_parameters),read_result(encoded)
            check('parameter '+repr(value)+' actual driver behavior preserved',a==z)
            parameter_records.append(dict(input=value_repr(value),original=a,candidate=z))
        save('parameter-cases.json',parameter_records)
        check('original resident connection identity retained',lib._connection() is connection)

        # Exact archived row reader exits its inner transaction before decode.
        ns=dict(core.__dict__);source=(SCALAR/'drop-flex-and-model.py').read_text()
        check('archived reader intact',fingerprint(SCALAR/'drop-flex-and-model.py')==load(SCALAR/'manifest.json')['drop-flex-and-model.py'])
        exec(compile(source,str(SCALAR/'drop-flex-and-model.py'),'exec'),ns)
        converter=Item._convert;life=[]
        for outer in [False,True]:
            for arm in ['original','candidate']:
                events=[]
                def observe(cls,key,value):
                    if key=='tracktotal':
                        with lib._tx_stack() as stack:depth=len(stack)
                        events.append(dict(key=key,host_depth=depth,sqlite_in_transaction=connection.in_transaction))
                    return converter(key,value)
                with ExitStack() as stack:
                    if outer:stack.enter_context(lib.transaction())
                    stack.enter_context(patch.object(Item,'_convert',classmethod(observe)))
                    if arm=='original':result=lib.items(query.MatchQuery('album_id',1))[0].tracktotal
                    else:
                        rows=ns['read_fixed_rows'](lib,Item,query.MatchQuery('album_id',1),lib.get_default_item_sort())
                        result=decode(rows)
                check(f'{outer}/{arm} decoder at original outer depth',len(events)==1 and events[0]['host_depth']==int(outer))
                life.append(dict(outer=outer,arm=arm,result=value_repr(result),events=events))
        check('both lifecycle pairs identical',all(life[i]['result']==life[i+1]['result'] and life[i]['events']==life[i+1]['events'] for i in [0,2]))
        save('lifecycle-cases.json',life)

        # Real original conversion effects/errors must survive the host codec.
        packet=lib.items(query.MatchQuery('album_id',1));rows=list(packet.rows);flex=list(packet.flex_rows)
        negative=[]
        for kind in ['field-conversion-effect','field-conversion-error','all-conversion-effect',
                     'all-conversion-error','getter-override','mtime-getter-error']:
            arms=[]
            for arm in ['original','candidate']:
                calls=[]
                def conversion(cls,key,value):
                    calls.append(key)
                    if kind=='all-conversion-error':raise RuntimeError(key+' conversion failure')
                    return converter(key,value)
                field_type=Item._type('tracktotal');field_convert=field_type.from_sql
                def field_conversion(value):
                    calls.append('tracktotal')
                    if kind=='field-conversion-error':raise RuntimeError('tracktotal conversion failure')
                    return field_convert(value)
                def getter(item):
                    calls.append('getter')
                    if kind=='mtime-getter-error':raise RuntimeError('mtime getter failure')
                    return 999
                with ExitStack() as stack:
                    if kind in ['getter-override','mtime-getter-error']:
                        stack.enter_context(patch.object(plugins,'item_field_getters',lambda:{'mtime' if kind=='mtime-getter-error' else 'tracktotal':getter}))
                    elif kind.startswith('field-'):stack.enter_context(patch.object(field_type,'from_sql',field_conversion))
                    else:stack.enter_context(patch.object(Item,'_convert',classmethod(conversion)))
                    result=outcome(lambda:original_decode(rows,flex,lib)) if arm=='original' else outcome(lambda:decode(rows))
                arms.append(dict(arm=arm,outcome=result,calls=calls))
            same=arms[0]['outcome']==arms[1]['outcome'] and arms[0]['calls']==arms[1]['calls']
            check(kind+' scoped equivalence/negative expectation',same==kind.startswith('field-'))
            negative.append(dict(case=kind,arms=arms,equal=same))
        effect=next(x for x in negative if x['case']=='all-conversion-effect')
        check('original initialization demands mtime before tracktotal',effect['arms'][0]['calls']==['mtime','tracktotal'] and effect['arms'][1]['calls']==['tracktotal'])
        save('codec-effect-controls.json',negative)
        check('all binding/lifecycle/control data retained',snapshot(path)==before)
        check('resident connection unchanged through controls',lib._connection() is connection)
        lib._close()
        check('original baseline database untouched',fingerprint(BASE/'100/library.db')==original_db_hash)
        save('source-witnesses.json',[dict(function=fn.__qualname__,file=str(Path(inspect.getsourcefile(fn)).relative_to(ROOT)),
            line=inspect.getsourcelines(fn)[1],sha256=fingerprint(inspect.getsourcefile(fn))) for fn in [core.Model.__init__,Item.update,core.Model._convert,core.Results.__getitem__,query.MatchQuery.col_clause]])
        save('report.json',dict(status='passed',checks=checks,decode_cases=len(records),parameter_cases=len(parameter_records),lifecycle_cases=len(life),
            result='Same resource/parameter/output model across systems. Beets raw row packet decoder matches original fixed-field conversion, including original precision loss and errors.',
            backend_admitted=False,selector_modified=False,performance_measured=False,
            remaining=['No safe omission of second-read errors established','No arbitrary plugin/constructor-effect equivalence',
                       'Model.__init__ dynamically calls Item.update; mtime read/conversion precedes tracktotal and is absent from direct scalar decoding',
                       'Resource roles are described but not yet compiled into a new source-bound selector/backend interface'],
            limits='Bounded source-bound codec experiment, not a general whole-region proof; existing SQLite and JDBC lifecycles retained'))
    except BaseException as exc:
        save('failure.json',dict(type=type(exc).__name__,message=str(exc),checks=checks));raise
    finally:
        save('manifest.json',{str(p.relative_to(out)):fingerprint(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        print(out)


if __name__=='__main__':main()
