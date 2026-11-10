#!/usr/bin/env python3
"""Reproducible, bytecode-only TPCC decompilation and compileability evidence.

No Java source input is read. Decompiler output is never patched. Successful
javac checks are only compileability checks, not proof of semantic equivalence.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checks_passed(manifest):
    commands = manifest.get('commands', [])
    outcomes = manifest.get('outcomes', {})
    if not commands or any(c.get('returncode') != 0 for c in commands):
        return False
    if any(not outcomes.get(side, {}).get('java_sources') or
           outcomes[side].get('decompile_returncode') != 0 or
           outcomes[side].get('recompile_returncode') != 0 for side in ['reference', 'candidate']):
        return False
    return manifest.get('candidate_app_extension', {}).get('recompile_returncode', 0) == 0


def extend_candidate_app(output, jdk):
    """Extend a retained attempt using its existing binary snapshot only."""
    output = output.resolve()
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    extension = output / 'candidate-app-extension'
    extension.mkdir(exist_ok=False)
    shutil.copy2(manifest_path, extension / 'manifest-before-extension.json')
    dependency = output / 'inputs/dependencies/candidate'
    selected = output / 'inputs/targets/candidate-app'
    for source in sorted((dependency / 'reversedb/tpcc').glob('CandidateApp*.class')):
        target = selected / source.relative_to(dependency)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest['inputs'].append({'source': str(source), 'snapshot': str(target), 'role': 'candidate-app-target', 'sha256': digest(target), 'bytes': target.stat().st_size})
    tool = output / 'inputs/tool/vineflower-1.12.0.jar'
    libs = sorted((output / 'inputs/dependencies/reference/lib').glob('*.jar'))
    sources = output / 'sources/candidate-app'
    sources.mkdir(parents=True)
    classes = output / 'recompiled/candidate-app-inclusive'
    classes.mkdir(parents=True)
    classpath = os.pathsep.join(str(x) for x in [dependency, *libs])
    commands = [('decompile-candidate-app', [jdk / 'java', '-Xmx1024m', '-XX:ActiveProcessorCount=2', '-jar', tool, '--thread-count=2', '--folder', '--bytecode-source-mapping=true', '--skip-extra-files=true', *('-e=' + str(x) for x in [dependency, *libs]), selected, sources])]
    for cls in sorted(selected.rglob('*.class')):
        name = str(cls.relative_to(selected).with_suffix('')).replace(os.sep, '.')
        commands.append(('javap-candidate-app-' + name, [jdk / 'javap', '-J-Xmx1024m', '-J-XX:ActiveProcessorCount=2', '-classpath', classpath, '-c', '-p', '-s', name]))
    for label, command in commands:
        extension_run(output, manifest, label, command)
    java_sources = sorted((output / 'sources/candidate').rglob('*.java')) + sorted(sources.rglob('*.java'))
    code = extension_run(output, manifest, 'recompile-candidate-app-inclusive', [jdk / 'javac', '-J-Xmx1024m', '-J-XX:ActiveProcessorCount=2', '-proc:none', '-implicit:none', '-sourcepath', output / 'empty-sourcepath', '-classpath', classpath, '-d', classes, *java_sources])
    manifest['candidate_app_extension'] = {'script_sha256': digest(Path(__file__)), 'input_source': 'retained original classpath snapshot', 'java_sources': [str(x) for x in java_sources], 'recompile_returncode': code}
    manifest['limitations'].append('CandidateApp was subsequently added from the retained binary snapshot; AppRuntime and JdbcTrace remain classpath-only.')
    manifest['outputs'] = [{'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size} for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'manifest.json' and 'inputs' not in path.relative_to(output).parts]
    manifest['status'] = 'passed' if checks_passed(manifest) else 'failed'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    if manifest['status'] != 'passed':
        raise SystemExit(1)


def extension_run(output, manifest, label, command):
    command = [str(x) for x in command]
    entry = {'label': label, 'argv': command, 'shell_display_only': shlex.join(command), 'cwd': str(output), 'started_unix': time.time()}
    manifest['commands'].append(entry)
    with (output / 'logs' / (label + '.stdout.txt')).open('w') as stdout, (output / 'logs' / (label + '.stderr.txt')).open('w') as stderr:
        result = subprocess.run(command, cwd=output, stdout=stdout, stderr=stderr, check=False)
    entry.update(returncode=result.returncode, ended_unix=time.time(), stdout=str(output / 'logs' / (label + '.stdout.txt')), stderr=str(output / 'logs' / (label + '.stderr.txt')))
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(label, result.returncode, flush=True)
    return result.returncode


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=root / 'work/tpcc/runs/c615d3357989/distribution/benchbase-postgres/benchbase.jar')
    parser.add_argument('--candidate', type=Path, default=root / 'work/tpcc/app-runs/b7157edda001/standalone-candidate')
    parser.add_argument('--tool', type=Path, default=root / 'work/tpcc/tools/vineflower-1.12.0/vineflower-1.12.0.jar')
    parser.add_argument('--jdk', type=Path, default=Path('/opt/homebrew/opt/openjdk/bin'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--extend-candidate-app', type=Path, help='Extend an existing evidence directory from its retained binary snapshot')
    args = parser.parse_args()
    if args.extend_candidate_app:
        extend_candidate_app(args.extend_candidate_app, args.jdk)
        return
    output = args.output or root / 'work/tpcc/decompilation' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'created_unix': time.time(), 'output': str(output), 'script': str(Path(__file__).resolve()), 'script_sha256': digest(Path(__file__)), 'inputs': [], 'commands': [], 'limits': {'java_max_heap_mib': 1024, 'active_processors': 2, 'decompiler_threads': 2}, 'limitations': ['Vineflower fallback, not REA/JEB decompilation.', 'Static decompilation does not observe execution.', 'Original source files are not inputs.', 'Recompilation is a syntax/type compatibility check only, not semantic equivalence.', 'Only five reference procedures plus TPCCProcedure, and five candidate services plus Sql and their nested classes are decompiled. External classpath is dependency evidence only.']}

    def save_manifest():
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    def copy(source, target, role):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest['inputs'].append({'source': str(source.resolve()), 'snapshot': str(target), 'role': role, 'sha256': digest(target), 'bytes': target.stat().st_size})
        return target

    def run(label, command):
        command = [str(x) for x in command]
        logs = output / 'logs'
        logs.mkdir(exist_ok=True)
        entry = {'label': label, 'argv': command, 'shell_display_only': shlex.join(command), 'cwd': str(output), 'started_unix': time.time()}
        manifest['commands'].append(entry)
        save_manifest()
        with (logs / (label + '.stdout.txt')).open('w') as stdout, (logs / (label + '.stderr.txt')).open('w') as stderr:
            result = subprocess.run(command, cwd=output, stdout=stdout, stderr=stderr, check=False)
        entry.update(returncode=result.returncode, ended_unix=time.time(), stdout=str(logs / (label + '.stdout.txt')), stderr=str(logs / (label + '.stderr.txt')))
        save_manifest()
        print(label, result.returncode, flush=True)
        return result.returncode

    expected = '1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d'
    if digest(args.tool) != expected:
        raise RuntimeError('Vineflower digest does not match independently supplied 1.12.0 identity')
    tool = copy(args.tool, output / 'inputs/tool/vineflower-1.12.0.jar', 'decompiler')
    manifest['jdk'] = {name: {'path': str((args.jdk / name).resolve()), 'sha256': digest(args.jdk / name)} for name in ('java', 'javac', 'javap')}
    run('java-version', [args.jdk / 'java', '-version'])
    run('javac-version', [args.jdk / 'javac', '-version'])
    reference_jar = copy(args.reference, output / 'inputs/dependencies/reference/benchbase.jar', 'reference-container-and-dependency')
    libs = [copy(path, output / 'inputs/dependencies/reference/lib' / path.name, 'reference-external-library') for path in sorted((args.reference.parent / 'lib').glob('*.jar'))]
    candidate_deps = output / 'inputs/dependencies/candidate'
    for path in sorted(args.candidate.rglob('*.class')):
        copy(path, candidate_deps / path.relative_to(args.candidate), 'candidate-classpath-only')
    reference_input = output / 'inputs/targets/reference'
    candidate_input = output / 'inputs/targets/candidate'
    transactions = ('NewOrder', 'Payment', 'OrderStatus', 'Delivery', 'StockLevel')
    reference_prefix = 'com/oltpbenchmark/benchmarks/tpcc/procedures/'
    with zipfile.ZipFile(reference_jar) as archive:
        for member in sorted(archive.namelist()):
            if member.endswith('.class') and member.startswith(reference_prefix) and member[len(reference_prefix):].split('$')[0].split('.')[0] in (*transactions, 'TPCCProcedure'):
                target = reference_input / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
                manifest['inputs'].append({'source': str(args.reference.resolve()) + '!/' + member, 'snapshot': str(target), 'role': 'reference-target', 'sha256': digest(target), 'bytes': target.stat().st_size})
    for path in sorted((args.candidate / 'reversedb/tpcc').glob('*.class')):
        if path.name.split('$')[0].split('.')[0] in (*(name + 'Service' for name in transactions), 'Sql'):
            copy(path, candidate_input / path.relative_to(args.candidate), 'candidate-target')
    save_manifest()
    outcomes = {}
    for name, inputs, dependencies in [('reference', reference_input, [reference_jar, *libs]), ('candidate', candidate_input, [candidate_deps, *libs])]:
        sources = output / 'sources' / name
        sources.mkdir(parents=True)
        outcomes[name] = {'decompile_returncode': run('decompile-' + name, [args.jdk / 'java', '-Xmx1024m', '-XX:ActiveProcessorCount=2', '-jar', tool, '--thread-count=2', '--folder', '--bytecode-source-mapping=true', '--skip-extra-files=true', *('-e=' + str(dep) for dep in dependencies), inputs, sources])}
        classpath = os.pathsep.join(str(x) for x in [inputs, *dependencies])
        for cls in sorted(inputs.rglob('*.class')):
            class_name = str(cls.relative_to(inputs).with_suffix('')).replace(os.sep, '.')
            run('javap-' + name + '-' + class_name, [args.jdk / 'javap', '-J-Xmx1024m', '-J-XX:ActiveProcessorCount=2', '-classpath', classpath, '-c', '-p', '-s', class_name])
        java_sources = sorted(sources.rglob('*.java'))
        classes = output / 'recompiled' / name
        classes.mkdir(parents=True)
        empty_sourcepath = output / 'empty-sourcepath'
        empty_sourcepath.mkdir(exist_ok=True)
        outcomes[name]['java_sources'] = [str(path) for path in java_sources]
        outcomes[name]['recompile_returncode'] = run('recompile-' + name, [args.jdk / 'javac', '-J-Xmx1024m', '-J-XX:ActiveProcessorCount=2', '-proc:none', '-implicit:none', '-sourcepath', empty_sourcepath, '-classpath', classpath, '-d', classes, *java_sources]) if java_sources else None
    manifest['outcomes'] = outcomes
    manifest['outputs'] = [{'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size} for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'manifest.json' and 'inputs' not in path.relative_to(output).parts]
    manifest['status'] = 'passed' if checks_passed(manifest) else 'failed'
    save_manifest()
    print(json.dumps({'output': str(output), 'outcomes': outcomes}, indent=2), flush=True)
    if manifest['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
