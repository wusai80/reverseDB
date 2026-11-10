"""Fixed development validation of source -> IR -> checked executable code."""
import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import unittest

from .model import Contract, MIN_I64, MAX_I64, Unsupported, observe
from .rewrite import propose, discover_regions
from .source_codegen import generate
from .source_examples import INPUTS, bundles
from .source_frontend import extract
from .source_runtime import invoke, load_original
from .sqlite_backend import execute
from .test_semantics import SemanticsTests
from .test_source_pipeline import SourcePipelineTests
from .verify import states, inputs, fault_plans


def write(path,value): path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def old_source_abstention(root):
    # A pre-existing development artifact, inspected but never executed here.
    path = root/'prototype/beets/prefix_consumer.py'
    text = path.read_text()
    findings = sorted({type(n).__name__ for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.If,ast.FunctionDef,ast.DictComp,ast.GeneratorExp,ast.ImportFrom))})
    try:
        extract({'legacy':text},('legacy','consume_packet'),{})
    except Unsupported as error:
        return dict(source=str(path.relative_to(root)),sha256=digest(path),accepted=False,reason=str(error),
            syntax_inventory=findings,executed=False,
            interpretation='Existing dynamic beets path is outside the closed-source language; this is abstention, not successful application extraction')
    raise AssertionError('Unexpected admission of dynamic beets path')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(); root=Path.cwd()
    args.out.mkdir(parents=True,exist_ok=False)
    snapshot=args.out/'inputs'; snapshot.mkdir()
    files=sorted(Path(__file__).parent.glob('*.py'))
    hashes={}
    for path in files:
        shutil.copyfile(path,snapshot/path.name)
        hashes[str(path.relative_to(root)) if path.is_absolute() else str(path)]=digest(path)
    core_freeze=root/'paper/region-core-freeze.json'
    frozen=json.loads(core_freeze.read_text())
    unchanged={name:digest(root/name)==sha for name,sha in frozen['sha256'].items()}
    changed=[name for name,ok in unchanged.items() if not ok]
    expected_correction=['prototype/region_semantics/rewrite.py']
    assert changed==expected_correction,(changed,expected_correction)
    write(args.out/'manifest.json',dict(schema='source-pipeline-development/v1',sources=hashes,
        previous_freeze_sha256=digest(core_freeze),previous_frozen_files_unchanged=unchanged,
        core_correction=dict(files=changed,reason='Typed syntax comparison rejects bool/int substitution',counterexample='evidence/source-pipeline/20261110-typed-equality-counterexample.json'),
        python=platform.python_version(),sqlite=sqlite3.sqlite_version,
        scope='Constructed closed-source development programs; not held-out applications, SQL fusion or performance'))
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in [SemanticsTests,SourcePipelineTests]])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    write(args.out/'tests.json',dict(tests=result.testsRun,failures=[str(x) for x in result.failures],errors=[str(x) for x in result.errors]))
    if not result.wasSuccessful(): raise SystemExit(1)
    rows=[]; mismatches=[]; artifact_dir=args.out/'cases'; artifact_dir.mkdir()
    configs=[(i,s) for i,s in itertools.product(inputs(),states())]
    configs += [(dict(key=k,other=1-k,value=v,enabled=e),{0:MIN_I64,1:MAX_I64,999:123}) for k,v,e in itertools.product([0,1],[MIN_I64,MAX_I64],[False,True])]
    for name,sources in bundles().items():
        program,witness=extract(sources,('app','run'),INPUTS)
        original=load_original(sources,('app','run'),INPUTS,program,witness)
        for strict in [True,False]:
            contract=Contract(strict)
            candidate,certificate=propose(program,contract)
            replacement,code,admission=generate(sources,('app','run'),INPUTS,program,witness,candidate,certificate,contract)
            case=name+('-strict' if strict else '-successful-read')
            directory=artifact_dir/case; directory.mkdir()
            source_dir=directory/'source';source_dir.mkdir()
            for module,text in sources.items(): (source_dir/(module+'.py')).write_text(text)
            (directory/'replacement.py').write_text(code)
            write(directory/'binding.json',dict(program=program,witness=witness,candidate=candidate,certificate=certificate,admission=admission,
                structural_regions=discover_regions(program),local_regions=discover_regions(program,local_only=True)))
            row=dict(case=case,cases=0,original_reads=0,replacement_reads=0,original_writes=0,replacement_writes=0,
                source_operations=len(program['ops']),candidate_operations=len(candidate['ops']),
                source_tuple_ops=sum(o['op']=='pack' for o in program['ops']),candidate_tuple_ops=sum(o['op']=='pack' for o in candidate['ops']))
            for values,state in configs:
                ir_inputs={witness['inputs'][k]:v for k,v in values.items()}
                for faults in fault_plans(program,contract):
                    orig=invoke(original,program,witness,values,state,faults,contract)
                    repl=invoke(replacement,candidate,witness,values,state,faults,contract,generated=True)
                    ref=observe(program,ir_inputs,state,faults,contract)
                    opt_ref=observe(candidate,ir_inputs,state,faults,contract)
                    sql=execute(program,ir_inputs,state,faults,contract)
                    opt_sql=execute(candidate,ir_inputs,state,faults,contract)
                    observations=[json.dumps(r['observation'],sort_keys=True) for r in [orig,repl,ref,opt_ref,sql,opt_sql]]
                    row['cases']+=1
                    for arm,data in [('original',orig),('replacement',repl)]:
                        for metric in ['reads','writes']:row[arm+'_'+metric]+=data['work'][metric]
                    if len(set(observations))!=1 or any(orig['work'][k]!=ref['work'][k] or repl['work'][k]!=opt_ref['work'][k] for k in ['reads','writes']):
                        mismatches.append(dict(case=case,inputs=values,state=state,faults=faults,original=orig,replacement=repl,reference=ref,optimized_reference=opt_ref,sqlite=sql,optimized_sqlite=opt_sql))
            rows.append(row)
    write(args.out/'mismatches.json',mismatches)
    abstention=old_source_abstention(root)
    write(args.out/'legacy-abstention.json',abstention)
    cases=sum(row['cases'] for row in rows)
    summary=dict(status='pass' if not mismatches else 'fail',unit_test_groups=result.testsRun,source_bundles=len(bundles()),contracts=2,
        input_state_configurations=len(configs),fault_matrix='All subsets of admissible DB sites with distinct site error codes',
        compared_execution_configurations=cases,original_python_executions=cases,generated_python_executions=cases,
        reference_ir_executions=2*cases,independent_sqlite_ir_executions=2*cases,mismatches=len(mismatches),rows=rows,
        legacy_abstention=abstention['accepted'] is False,previous_freeze_changes=changed,
        g1_status='closed-source development demonstration complete; existing application extraction and replacement binding incomplete',
        performance_measured=False,held_out_transfer=False,sql_fusion=False)
    write(args.out/'summary.json',summary)
    # Bind generated source, witnesses and actual original source bytes as well.
    artifacts={str(p.relative_to(args.out)):digest(p) for p in sorted(args.out.rglob('*')) if p.is_file()}
    write(args.out/'artifact-hashes.json',artifacts)
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
    if mismatches: raise SystemExit(1)


if __name__=='__main__': main()
