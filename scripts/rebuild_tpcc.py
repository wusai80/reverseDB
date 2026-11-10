#!/usr/bin/env python3
"""Compile unchanged decompiler output and assemble runtimes without original target fallbacks."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECOMP = ROOT / 'work/tpcc/decompilation/20261103T125711-a3efc54a'
SUPPORT = ROOT / 'work/tpcc/app-runs/8729d2283fdc'
DIST = ROOT / 'work/tpcc/runs/c615d3357989/distribution/benchbase-postgres'
JDK = Path('/opt/homebrew/opt/openjdk/bin')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_bundle(bundle):
    bundle = Path(bundle)
    manifest = json.loads((bundle / 'build.json').read_text())
    if manifest['status'] != 'passed':
        raise ValueError('Rebuild did not pass')
    for path, expected in manifest['sources'].items():
        if digest(bundle / path) != expected:
            raise ValueError('Rebuilt source changed: ' + path)
    for path, expected in manifest['runtime_files'].items():
        if digest(bundle / path) != expected:
            raise ValueError('Rebuilt runtime file changed: ' + path)
    for path, expected in manifest['dependencies'].items():
        if digest(path) != expected:
            raise ValueError('Rebuild dependency changed: ' + path)
    for key in ['candidate-classes', 'reference-adapter']:
        actual = {str(p.relative_to(bundle)) for p in (bundle / key).rglob('*.class')}
        expected = {p for p in manifest['runtime_files'] if p.startswith(key + '/')}
        if actual != expected:
            raise ValueError('Unexpected runtime classes in ' + key)
    with zipfile.ZipFile(bundle / 'reference.jar') as archive:
        for row in manifest['rebuilt_classes']:
            if row['engine'] == 'reference':
                raw = archive.read(row['member'])
            else:
                raw = (bundle / 'candidate-classes' / row['member']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Original/stale target fallback: ' + row['member'])
    return manifest


def verify_loaded_classes(run, bundle, require_all=True):
    run, bundle = Path(run), Path(bundle).resolve()
    manifest = validate_bundle(bundle)
    record = json.loads((run / 'run.json').read_text())
    checked = []
    for row in manifest['rebuilt_classes']:
        name = row['member'].removesuffix('.class').replace('/', '.')
        origin = bundle / ('reference.jar' if row['engine'] == 'reference' else 'candidate-classes')
        loaded_in = []
        for database in record['cases']:
            if not database.endswith(row['engine']):
                continue
            lines = (run / database / 'class-load.log').read_text().splitlines()
            found = [line for line in lines if ' ' + name + ' source: ' in line]
            if found:
                locations = [urlparse(line.split(' source: ', 1)[1].strip()) for line in found]
                if any(p.scheme != 'file' or p.netloc or Path(unquote(p.path)).resolve() != origin for p in locations):
                    raise ValueError('Wrong class origin: ' + name)
                loaded_in.append(database)
        if not loaded_in and require_all:
            raise ValueError('Rebuilt class never loaded: ' + name)
        if loaded_in:
            checked.append({**row, 'loaded_in': loaded_in, 'load_origin': str(origin)})
    return {'status': 'passed', 'class_count': len(checked), 'classes': checked,
            'require_all': require_all,
            'scope': 'Loaded selected classes originate in checked rebuilt artifacts; unchanged framework and dependencies remain explicit'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decompilation', type=Path, default=DEFAULT_DECOMP)
    args = parser.parse_args()
    decomp = args.decompilation.resolve()
    prior = json.loads((decomp / 'manifest.json').read_text())
    out = ROOT / 'work/tpcc/rebuilds' / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    record = {'status': 'building', 'decompilation': str(decomp), 'manifest_sha256': digest(decomp / 'manifest.json'),
              'script_sha256': digest(__file__), 'commands': [], 'sources': {}, 'support_classes': {},
              'dependencies': {}, 'rebuilt_classes': [], 'runtime_files': {},
              'scope': 'Six reference classes and ten candidate classes rebuilt from unchanged decompiled Java; common framework, reference adapter and unselected library classes retained'}
    def save():
        (out / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    def execute(command, name):
        command = list(map(str, command)); record['commands'].append(command); save()
        with (out / name).open('w') as f:
            subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=180)
    try:
        # Select only Java outputs whose bytes match the preceding decompiler manifest.
        for item in prior['outputs']:
            src = Path(item['path'])
            if src.suffix != '.java':
                continue
            if digest(src) != item['sha256']:
                raise ValueError('Decompiler output was edited: ' + str(src))
            rel = src.relative_to(decomp / 'sources')
            side = 'reference' if rel.parts[0] == 'reference' else 'candidate'
            target = out / 'sources' / side / Path(*rel.parts[1:])
            if target.exists():
                raise ValueError('Duplicate decompiled source')
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, target)
            record['sources'][str(target.relative_to(out))] = item['sha256']
        if len(record['sources']) != 13:
            raise ValueError('Expected thirteen unchanged decompiled Java files')
        candidate = out / 'candidate-classes'; candidate.mkdir()
        reference = out / 'reference-classes'; reference.mkdir()
        adapter = out / 'reference-adapter'; adapter.mkdir()
        empty = out / 'empty-sourcepath'; empty.mkdir()
        targets = {'reference': {}, 'candidate': {}}
        for item in prior['inputs']:
            if item['role'] in ['reference-target', 'candidate-target', 'candidate-app-target']:
                side = 'reference' if item['role'] == 'reference-target' else 'candidate'
                prefix = 'com/oltpbenchmark/benchmarks/tpcc/procedures/' if side == 'reference' else 'reversedb/tpcc/'
                targets[side][prefix + Path(item['snapshot']).name] = item['sha256']
        support_lock = json.loads((SUPPORT / 'class-sha256.json').read_text())
        for relative, expected in support_lock.items():
            src = SUPPORT / relative
            if digest(src) != expected:
                raise ValueError('Frozen framework changed')
            if relative.startswith('standalone-candidate/'):
                member = relative.removeprefix('standalone-candidate/')
                if member in targets['candidate']:
                    continue  # Never place original business classes on the candidate compile/runtime path.
                dst = candidate / member
            else:
                dst = adapter / relative.removeprefix('classes/')
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
            record['support_classes'][str(dst.relative_to(out))] = expected
        libs = sorted((DIST / 'lib').glob('*.jar'))
        expected_dependencies = {Path(i['source']).name: i['sha256'] for i in prior['inputs'] if i['role'] == 'reference-external-library'}
        for lib in libs:
            if digest(lib) != expected_dependencies[lib.name]:
                raise ValueError('Dependency differs from decompilation')
            record['dependencies'][str(lib)] = digest(lib)
        candidate_cp = os.pathsep.join(map(str,[candidate, DIST/'lib/json-20240303.jar', DIST/'lib/postgresql-42.7.4.jar']))
        execute([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,
                 '-cp',candidate_cp,'-d',candidate,*sorted((out/'sources/candidate').rglob('*.java'))], 'compile-candidate.log')
        jar = DIST/'benchbase.jar'
        original_jar_hash = next(i['sha256'] for i in prior['inputs'] if i['role']=='reference-container-and-dependency')
        if digest(jar) != original_jar_hash:
            raise ValueError('Reference JAR changed')
        record['original_reference_jar_sha256'] = original_jar_hash
        execute([JDK/'javac','--release','23','-proc:none','-implicit:none','-sourcepath',empty,
                 '-cp',os.pathsep.join(map(str,[jar,*libs])),'-d',reference,
                 *sorted((out/'sources/reference').rglob('*.java'))], 'compile-reference.log')
        for side, directory in [('candidate',candidate),('reference',reference)]:
            actual = {str(p.relative_to(directory)) for p in directory.rglob('*.class')}
            expected = set(targets[side])
            if side == 'candidate':
                expected |= {p.removeprefix('candidate-classes/') for p in record['support_classes'] if p.startswith('candidate-classes/')}
            if actual != expected:
                raise ValueError('Unexpected or missing generated classes: ' + side)
            for member, old_hash in targets[side].items():
                new_hash = digest(directory / member)
                record['rebuilt_classes'].append(dict(engine=side,member=member,sha256=new_hash,original_sha256=old_hash))
        with zipfile.ZipFile(jar) as original, zipfile.ZipFile(out/'reference.jar','w',compression=zipfile.ZIP_DEFLATED) as rebuilt:
            if len(original.namelist()) != len(set(original.namelist())):
                raise ValueError('Duplicate reference archive members')
            for item in original.infolist():
                data = (reference/item.filename).read_bytes() if item.filename in targets['reference'] else original.read(item)
                rebuilt.writestr(item, data)
        for path in [out/'reference.jar', *candidate.rglob('*.class'), *adapter.rglob('*.class')]:
            record['runtime_files'][str(path.relative_to(out))] = digest(path)
        record['status'] = 'passed'; save(); validate_bundle(out)
    except BaseException as error:
        record.update(status='failed',error=repr(error)); save(); raise
    print(json.dumps({'status':record['status'],'bundle':str(out),'classes':len(record['rebuilt_classes'])}))


if __name__ == '__main__':
    main()
