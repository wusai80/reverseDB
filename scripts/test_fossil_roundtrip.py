"""Recompile two recovered native functions and reuse the common admission gate.

The original bodies are removed from the linked db object. Other native regions,
including a decompiler return-type ambiguity, are retained. No optimization.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from test_fossil_feasibility import state
from region_contracts import describe, inspect_description_evidence

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'work/fossil/build-original'
BASE = ROOT / 'evidence/fossil-feasibility/20261110-v2'
RECOVERY = ROOT / 'evidence/fossil-recovery/20261110-v1/functions'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2, sort_keys=True) + '\n')
def load(p): return json.loads(p.read_text())
def ref(p): return dict(path=str(p.relative_to(ROOT)), sha256=sha(p))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve(); out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, value):
        if not value: raise AssertionError(name)
        checks.append(name)
    def run(argv, name, cwd=BUILD, env=None):
        p = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True, timeout=120)
        (out / (name + '.stdout')).write_bytes(p.stdout); (out / (name + '.stderr')).write_bytes(p.stderr)
        check(name + ' successful exit', p.returncode == 0)
        return p
    save(out / 'protocol.json', dict(scope='native recovery/recompilation and conservative existing admission, not optimization',
        functions=['db_step', 'db_column_int'], transformations=['Mach-O symbol underscore normalization only', 'SQLite type declarations from pinned public header'],
        abi='Darwin arm64 LP64; recovered long holds the incoming Stmt pointer; offsets 0x20/0x38/0x3c retained',
        runs='100 and 2000 complete commits, default/full/hash/branch-filtered/reversed formats, original and recovered binaries',
        unsupported='db_column_text recovered as void despite pointer-returning tail call; not linked',
        stop='No native optimization without checked pointer/SQLite callback/effect semantics; no new project-specific optimizer'))
    try:
        recovery = load(RECOVERY / 'recovery.json')
        check('all ten native recoveries completed', len(recovery['functions']) == 10 and all(x['completed'] for x in recovery['functions']))
        check('native text wrapper type ambiguity retained', 'void _db_column_text(' in (RECOVERY / 'db_column_text.c').read_text())
        bodies = [(RECOVERY / (n + '.c')).read_text() for n in ['db_step', 'db_column_int']]
        generated = '#include "sqlite3.h"\n' + '\n'.join(bodies)
        for name in ['db_step', 'db_column_int', 'sqlite3_step', 'sqlite3_column_int']:
            generated = generated.replace('_' + name + '(', name + '(')
        (out / 'recovered.c').write_text(generated)
        original_db = (BUILD / 'bld/db_.c').read_text()
        modified = original_db
        for name, args in [('db_step', r'Stmt \*pStmt'), ('db_column_int', r'Stmt \*pStmt, int N')]:
            modified, count = re.subn(r'\nint ' + name + r'\(' + args + r'\)\{.*?\n\}',
                '\n/* Original ' + name + ' removed: linked from archived native recovery. */', modified, flags=re.S)
            check(name + ' original body removed exactly once', count == 1)
        (out / 'db-without-targets.c').write_text(modified)
        (out / 'db.diff').write_text(''.join(difflib.unified_diff(original_db.splitlines(True), modified.splitlines(True), fromfile='original/db_.c', tofile='roundtrip/db_.c')))
        log = (BUILD / 'build.log').read_text().splitlines()
        argv = shlex.split(next(x for x in log if x.startswith('cc ') and x.endswith(' -c bld/db_.c')))
        argv[argv.index('-o') + 1] = str(out / 'db.o'); argv[-1] = str(out / 'db-without-targets.c')
        run(argv, 'compile-db')
        argv[argv.index('-o') + 1] = str(out / 'recovered.o'); argv[-1] = str(out / 'recovered.c')
        run(argv, 'compile-recovered')
        link = shlex.split(next(x for x in reversed(log) if x.startswith('cc ') and ' -o fossil ' in x))
        link = [str(out / 'db.o') if x == 'bld/db.o' else x for x in link]
        executable = out / 'fossil-roundtrip'; link[link.index('-o') + 1] = str(executable); link.append(str(out / 'recovered.o'))
        run(link, 'link-roundtrip')
        for name in ['db_step', 'db_column_int']:
            run(['objdump', '--disassemble-symbols=_' + name, executable], name + '-roundtrip-assembly')
        nm = run(['nm', out / 'db.o'], 'db-symbols').stdout.decode()
        generated_nm = run(['nm', out / 'recovered.o'], 'recovered-symbols').stdout.decode()
        for name in ['db_step', 'db_column_int']:
            check(name + ' only defined in recovered object', not re.search(r'\b[Tt] _' + name + r'$', nm, re.M) and bool(re.search(r'\b[Tt] _' + name + r'$', generated_nm, re.M)))
        cases = []
        variants = [('default', []), ('full', ['--full']), ('hash', ['--format', '%H']),
                    ('branch', ['--full', '--branch', 'feature']), ('reverse', ['--full', '--reverse'])]
        for size in [100, 2000]:
            for name, options in variants:
                records = []
                for arm, binary in [('original', BUILD / 'fossil'), ('roundtrip', executable)]:
                    directory = out / f'{size}-{name}-{arm}'; directory.mkdir(); (directory / 'home').mkdir()
                    db = directory / 'repository.fossil'; shutil.copyfile(BASE / str(size) / 'repository.fossil', db)
                    expected = load(BASE / str(size) / 'database-state.json')
                    env = dict(os.environ, FOSSIL_HOME=str(directory / 'home'), LC_ALL='C', TZ='UTC')
                    command = [binary, 'timeline', 'before', '2030-01-01T00:00:00Z', '-R', db, '-n', '0', '-W', '0', '--utc', *options]
                    p = run(command, f'{size}-{name}-{arm}', cwd=directory, env=env)
                    check(f'{size}-{name}-{arm} full state', state(db) == expected)
                    records.append(dict(arm=arm, output_sha256=hashlib.sha256(p.stdout).hexdigest(), stdout=p.stdout))
                check(f'{size}-{name} exact output', records[0]['stdout'] == records[1]['stdout'])
                cases.append(dict(size=size, variant=name, hashes=[r['output_sha256'] for r in records]))
        # Bind observed/native facts to the SAME source/effect/admission interface.
        # Unknown costs stay unknown: measurements of one data set are not a
        # universal affine call model, and C pointers are not integer wire values.
        nodes = []
        specs = [
            ('sqlite_producer', {'request': 'native_request'}, {'row': 'borrowed_sqlite_row'}, ['sqlite_udf_effects', 'read_order', 'errors']),
            ('row_materialization', {'row': 'borrowed_sqlite_row'}, {'values': 'borrowed_text_and_integer_fields'}, ['pointer_lifetime', 'sqlite_conversion_and_oom']),
            ('display_construction', {'values': 'borrowed_text_and_integer_fields'}, {'text': 'owned_native_buffer'}, ['configuration_reads', 'callbacks', 'allocation_failure', 'retained_branch_effects']),
            ('format_and_output', {'values': 'borrowed_text_and_integer_fields', 'text': 'owned_native_buffer'}, {'response': 'ordered_output_bytes'}, ['full_output', 'format_and_control_dependence', 'buffer_ownership'])]
        for name, uses, defines, obligations in specs:
            nodes.append(dict(id=name, uses=uses, defines=defines, queries=[], transaction='original-sqlite-lifecycle',
                body_known=False, java_calls=None, database=None, requires=obligations,
                retention_reason='No checked native lowering; source/decompiler observations do not discharge effects'))
        program = dict(nodes=nodes, edges=[dict(before=nodes[i]['id'], after=nodes[i+1]['id'], kind='source_order', resource='original execution') for i in range(3)],
                       external_inputs={'request': 'native_request'}, parameters={}, wire_types={}, max_boundary_values=None)
        unresolved = ['Native pointer and ownership semantics outside existing scalar/Python frontend',
                      'Wiki conversion performs configuration reads and data-dependent callbacks',
                      'Recovered pointer-returning wrapper has unresolved decompiler type',
                      'Original JDBC-call objective has no validated native latency interpretation']
        description = describe(program, scope=dict(entry='Fossil timeline', execution_domain='Pinned binary and SQLite deployment; arbitrary supported repository data, not only observed rows',
            interface_inputs=program['external_inputs'], interface_outputs={'response': 'ordered_output_bytes'},
            observables=['full output', 'all persistent data', 'SQL/errors', 'pointer lifetime and effects']),
            provenance=[ref(RECOVERY / 'recovery.json'), ref(RECOVERY / 'print_timeline.c'), ref(BUILD / 'fossil')],
            evidence=[ref(BASE / 'summary.json')], unresolved=unresolved)
        decision = inspect_description_evidence(description, ROOT)
        check('same common gate refuses unsupported native optimization', decision['decision']['selection'] is None)
        save(out / 'common-description.json', description); save(out / 'common-decision.json', decision)
        core = ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']
        for name in core:
            check(name + ' first-project algorithm unchanged', sha(ROOT / 'scripts' / name) == sha(ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts' / name))
        save(out / 'summary.json', dict(status='pass', checks=checks, native_functions_recovered=10, functions_recompiled=2,
            original_function_bodies_removed=2, whole_application_cases=cases, whole_application_executions=20,
            complete_data_preserved=True, type_ambiguity_retained=True, shared_algorithm_changed=False,
            optimization_admitted=False, candidate_speedup_measured=False, common_decision=decision['decision']))
    except BaseException as error:
        save(out / 'failure.json', dict(type=type(error).__name__, message=str(error), checks=checks)); raise
    finally:
        target = out / 'inputs' / 'scripts'; target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(__file__, target / Path(__file__).name)
        save(out / 'manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json'})


if __name__ == '__main__': main()
