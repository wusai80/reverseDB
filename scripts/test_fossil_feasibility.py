"""Pinned Fossil baseline and selected-module native cost observation.

No candidate is inferred from low coverage. Full repository snapshots and exact
outputs accompany each run. Instrumented costs are diagnostic, not speedups.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/fossil'
SOURCE = WORK / 'source-1573b8e6'
BUILD = WORK / 'build-original'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value): path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
def load(path): return json.loads(path.read_text())


def state(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            rows = [[{'bytes': x.hex()} if isinstance(x, bytes) else x for x in row]
                    for row in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
            tables[name] = sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False))
    return dict(schema=[list(x) for x in schema], tables=tables)


def stream(n):
    result = bytearray()
    parents = {}
    for i in range(1, n + 1):
        content = f'file version {i}\n完整内容\n'.encode()
        message = f'Change {i}: <b>metadata</b> [https://example.invalid|link] café 中文\n'.encode()
        branch = 'master' if i % 5 else 'feature'
        author = f'user{i % 7}'
        result.extend(f'blob\nmark :{2*i}\ndata {len(content)}\n'.encode() + content + b'\n')
        result.extend(f'commit refs/heads/{branch}\nmark :{2*i+1}\ncommitter {author} <{author}@example.invalid> {1700000000+i*60} +0000\ndata {len(message)}\n'.encode() + message)
        parent = parents.get(branch, parents.get('master'))
        if parent: result.extend(f'from :{parent}\n'.encode())
        result.extend(f'M 100644 :{2*i} file-{i%11}.txt\n\n'.encode())
        parents[branch] = 2*i+1
    # Fossil's importer terminates at EOF; it does not accept Git's done token.
    return bytes(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    checks, executions, commands = [], [], []
    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)
    env = dict(os.environ, FOSSIL_HOME=str(out / 'home'), LC_ALL='C', TZ='UTC')
    (out / 'home').mkdir()
    def run(argv, name, *, cwd=out, extra=None):
        start = time.perf_counter_ns()
        p = subprocess.run([str(x) for x in argv], cwd=cwd, env=dict(env, **(extra or {})), capture_output=True, timeout=180)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        (out / (name + '.stdout')).write_bytes(p.stdout)
        (out / (name + '.stderr')).write_bytes(p.stderr)
        commands.append(dict(name=name, argv=[str(x) for x in argv], exit_code=p.returncode, process_wall_ms=elapsed))
        save(out / 'commands.json', commands)
        check(name + ' exits successfully', p.returncode == 0)
        return p, elapsed
    save(out / 'protocol.json', dict(project='Fossil 2.28', sizes=[100, 2000],
        variants=['default', 'full', 'hash'], commits='fixed Git fast-import stream, two branches, seven authors, Unicode and wiki-markup comments',
        date='2030-01-01T00:00:00Z', order='fixed source order; full timeline without row limit',
        schedule='one warmup, two plain runs, one SQL trace per size/variant; one native instrumented run per 2000-row variant',
        native_modules=['timeline', 'db', 'wikiformat', 'printf', 'blob'],
        timing='plain whole-process wall time includes startup, DB open and captured output; descriptive original-only screen',
        probe='selected-module instrumented inclusive/exclusive time; overhead remains; not total application CPU shares',
        method='existing source/effect/cost/admission pipeline; no new deletion rules',
        no_data_pruning=True, no_optimization=True, no_speedup_claim=True, stop='fixed screen once; unknown native semantics retained'))
    try:
        hashes = load(WORK / 'source-manifest.json')
        check('pinned source unchanged', all(sha(SOURCE / p) == h for p, h in hashes.items()))
        shutil.copyfile(WORK / 'provenance.json', out / 'provenance.json')
        original = BUILD / 'fossil'
        run([original, 'version', '-v'], 'version')
        # Reuse the exact original compilation and link arguments; only the
        # selected diagnostic objects gain entry/exit probes.
        profile = out / 'profile-build'; profile.mkdir()
        log = (BUILD / 'build.log').read_text().splitlines()
        objects = {}
        for module in ['timeline', 'db', 'wikiformat', 'printf', 'blob']:
            line = next(x for x in log if x.startswith('cc ') and x.endswith(' -c bld/' + module + '_.c'))
            argv = shlex.split(line); oi = argv.index('-o') + 1
            target = profile / (module + '.o'); objects[argv[oi]] = str(target); argv[oi] = str(target)
            argv.append('-finstrument-functions')
            run(argv, 'compile-probe-' + module, cwd=BUILD)
        probe = ROOT / 'prototype/native_cost_probe.c'
        run(['cc', '-O2', '-c', probe, '-o', profile / 'probe.o'], 'compile-cost-probe')
        link = shlex.split(next(x for x in reversed(log) if x.startswith('cc ') and ' -o fossil ' in x))
        link = [objects.get(x, x) for x in link]
        instrumented = profile / 'fossil-profile'
        link[link.index('-o') + 1] = str(instrumented); link.append(str(profile / 'probe.o'))
        run(link, 'link-profile', cwd=BUILD)
        symbols, _ = run(['nm', '-n', instrumented], 'profile-symbols')
        headers, _ = run(['otool', '-l', instrumented], 'profile-load-commands')
        textbase = int(re.search(r'segname __TEXT\s+vmaddr (0x[0-9a-fA-F]+)', headers.stdout.decode())[1], 16)
        names = {}
        for line in symbols.stdout.decode().splitlines():
            match = re.match(r'^([0-9a-fA-F]+)\s+[tT]\s+(.+)$', line)
            if match: names[int(match[1], 16) - textbase] = match[2].removeprefix('_')
        binary_hashes = {str(p.relative_to(ROOT)): sha(p) for p in [original, instrumented]}
        save(out / 'binary-hashes.json', binary_hashes)
        for size in [100, 2000]:
            folder = out / str(size); folder.mkdir()
            feed = folder / 'history.git-export'; feed.write_bytes(stream(size))
            database = folder / 'repository.fossil'
            run([original, 'import', '--git', '--no-vacuum', database, feed], f'import-{size}')
            before = state(database); save(folder / 'database-state.json', before)
            with sqlite3.connect(database) as db:
                check(f'{size} complete check-ins', db.execute("SELECT count(*) FROM event WHERE type='ci'").fetchone()[0] == size)
                check(f'{size} seven users', db.execute('SELECT count(DISTINCT user) FROM event').fetchone()[0] == 7)
            for variant, options in [('default', []), ('full', ['--full']), ('hash', ['--format', '%H'])]:
                name = f'{size}-{variant}'
                arguments = ['timeline', 'before', '2030-01-01T00:00:00Z', '-R', database, '-n', '0', '-W', '0', '--utc'] + options
                golden, _ = run([original, *arguments], name + '-warmup')
                for repeat in range(2):
                    value, ms = run([original, *arguments], name + f'-plain-{repeat}')
                    check(name + f' plain {repeat} complete output', value.stdout == golden.stdout)
                    executions.append(dict(size=size, variant=variant, stage='plain', repeat=repeat, wall_ms=ms, output_sha256=hashlib.sha256(value.stdout).hexdigest()))
                traced, _ = run([original, *arguments, '--sqltrace'], name + '-sqltrace')
                check(name + ' SQL trace preserves output', traced.stdout == golden.stdout)
                if size == 2000:
                    raw = out / (name + '-profile.raw')
                    value, ms = run([instrumented, *arguments], name + '-profile', extra={'REVERSEDB_PROFILE': str(raw)})
                    check(name + ' native probe preserves output', value.stdout == golden.stdout)
                    lines = raw.read_text().splitlines(); head = lines[0].split(); base = int(head[1], 16)
                    check(name + ' native stack balanced', head[3] == '0' and head[5] == '0')
                    rows = []
                    for line in lines[1:]:
                        address, calls, inclusive, exclusive = line.split()
                        offset = int(address, 16) - base
                        check(name + ' native symbol resolved ' + hex(offset), offset in names)
                        rows.append(dict(function=names[offset], calls=int(calls), inclusive_ms=int(inclusive)/1e6, exclusive_ms=int(exclusive)/1e6))
                    save(out / (name + '-profile.json'), sorted(rows, key=lambda x: -x['inclusive_ms']))
                    executions.append(dict(size=size, variant=variant, stage='instrumented', wall_ms=ms, output_sha256=hashlib.sha256(value.stdout).hexdigest()))
                check(name + ' complete repository retained', state(database) == before)
                print(name + ' complete', flush=True)
        check('application source still unchanged', all(sha(SOURCE / p) == h for p, h in hashes.items()))
        check('executed binaries unchanged', all(sha(ROOT / p) == h for p, h in binary_hashes.items()))
        save(out / 'summary.json', dict(status='pass', checks=checks, measurements=executions,
            plain_runs=12, instrumented_runs=3, optimized=False, full_data_preserved=True,
            decompilation_status='separate recovery step required', performance_claim='Original-only cost screen; no candidate speedup'))
    except BaseException as error:
        save(out / 'failure.json', dict(type=type(error).__name__, message=str(error), checks=checks)); raise
    finally:
        for source in [Path(__file__), ROOT / 'prototype/native_cost_probe.c']:
            target = out / 'inputs' / source.relative_to(ROOT); target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        save(out / 'manifest.json', {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json'})


if __name__ == '__main__': main()
