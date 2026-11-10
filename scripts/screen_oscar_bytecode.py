#!/usr/bin/env python3
"""Read .pyc facts for a pinned candidate; do not import or execute its application."""
import dis
import hashlib
import importlib.machinery
import json
from pathlib import Path
import py_compile
import shutil
import sys
import types
import uuid

ROOT=Path(__file__).resolve().parents[1]
COMMIT='a5ed3c9c17d4cf6ee18aa9795b493172416ab68b'
TARGETS={
    'src/oscar/apps/order/utils.py':['OrderCreator.place_order','OrderCreator.create_line_models',
        'OrderCreator.create_line_price_models','OrderCreator.create_line_attributes','OrderCreator.update_stock_records'],
    'src/oscar/apps/partner/abstract_models.py':['AbstractStockRecord.allocate']}


def main():
    if sys.version_info[:2]!=(3,12):raise RuntimeError('Use the explicitly selected local CPython 3.12 frontend')
    source=Path(sys.argv[1]).resolve();inputs=json.loads((source/'selection-inputs.json').read_text())
    assert inputs['commit']==COMMIT
    out=ROOT/'evidence/nonjava-selection'/('20261106-'+uuid.uuid4().hex[:12]);out.mkdir(parents=True)
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    report=dict(status='running',commit=COMMIT,interpreter=sys.version,executable=sys.executable,
                interpreter_sha256=digest(Path(sys.executable)),source_inputs=inputs['files'],checks=[],
                application_executed=False,database_runs=0,optimizer_modified=False)
    def check(name,ok):
        assert ok,name
        report['checks'].append(name)
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    try:
        shutil.copy2(__file__,out/'screen_oscar_bytecode.py')
        for name,entry in inputs['files'].items():check('pinned source '+name,digest(source/name)==entry['sha256'])
        recovered={}
        def walk(code):
            recovered[code.co_qualname]=code
            for value in code.co_consts:
                if isinstance(value,types.CodeType):walk(value)
        methods=[]
        for name,targets in TARGETS.items():
            pyc=out/(Path(name).stem+'.pyc')
            py_compile.compile(str(source/name),cfile=str(pyc),dfile='pinned-oscar/'+name,doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            code=importlib.machinery.SourcelessFileLoader('inspection_only',str(pyc)).get_code('inspection_only')
            walk(code)
            for target in targets:
                check('code object recovered '+target,target in recovered)
                code=recovered[target];instructions=list(dis.get_instructions(code))
                methods.append(dict(name=target,source=name,first_line=code.co_firstlineno,
                    instructions=[dict(offset=i.offset,opcode=i.opname,argument=i.argrepr,line=i.starts_line,jump_target=i.is_jump_target) for i in instructions],
                    lexical_names=list(code.co_names),exception_table_hex=code.co_exceptiontable.hex(),
                    authority='actual generated .pyc disassembly; lexical names are not resolved runtime callees'))
        allocate=recovered['AbstractStockRecord.allocate']
        check('update and refresh names survive bytecode recovery',{'update','refresh_from_db','pre_save_signal','post_save_signal'}.issubset(allocate.co_names))
        check('per-line loop present',any(i.opname=='FOR_ITER' for i in dis.get_instructions(recovered['OrderCreator.place_order'])))
        check('atomic transaction and order signal retained in input',{'atomic','order_placed','send'}.issubset(recovered['OrderCreator.place_order'].co_names))
        save('bytecode-facts.json',dict(methods=methods,limits=['generated locally from verified source, not an independently distributed upstream binary',
            'no application import, runtime graph, effects proof or source reconstruction roundtrip',
            'source used for ground truth; later analysis must bind interpreter, Django/driver versions and actual configured receivers']))
        save('selection.json',dict(project='Django Oscar',language='Python',database='PostgreSQL for the next baseline',commit=COMMIT,
            phase='selected for bounded feasibility, not admitted optimization',
            first_entry='OrderCreator.place_order',first_local_question='Can repeated database execution/marshalling be removed while preserving original model and signal effects?',
            hypotheses=['per-line order/price/attribute writes may permit source-bound cross-layer fusion',
                        'stock allocation UPDATE followed by refresh is a concrete reuse/returning question, not an authorized read deletion'],
            mandatory_retention=['complete data and Python Decimal behavior','save hooks and registered signals','object identity and original refresh behavior',
                                 'deterministic stock lock order','transaction boundaries, permissions and failure behavior'],
            shared_method='same source-bound stages/effects/ABI admission and ordered-placement-dp/v1; map cost to measured DB-API execute boundaries, do not substitute ORM expression counts',
            alternatives=[dict(project='InvenTree',language='Python',decision='reserve; inspected lifecycle paths combine transitions, scheduled pricing and completion effects'),
                          dict(project='osquery',language='C++',decision='defer; OS virtual-table instrumentation is a different workload from business transaction fusion')],
            stop='first establish one original placement baseline and bytecode-to-SQL witness; if no bounded safe region remains, stop before whole-system expansion',
            performance_claim=None,alfio='closed boundary case, not reopened'))
        report.update(status='passed',recovered_target_methods=len(methods))
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save('run.json',report);save('manifest.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(json.dumps(dict(status=report['status'],output=str(out),checks=len(report['checks']),python=sys.version.split()[0])))


if __name__=='__main__':main()
