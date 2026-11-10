"""Offline audit of native source/binary provenance, full state and outputs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct

from test_fossil_feasibility import state

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/fossil'
BASE = ROOT / 'evidence/fossil-feasibility/20261110-v2'
REC = ROOT / 'evidence/fossil-recovery/20261110-v1'
ROUND = ROOT / 'evidence/fossil-roundtrip/20261110-v1'


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, data): p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def text_section(path):
    raw = path.read_bytes()
    header = struct.unpack_from('<8I', raw)
    assert header[0] == 0xfeedfacf
    offset = 32
    for _ in range(header[4]):
        command, size = struct.unpack_from('<II', raw, offset)
        if command == 0x19:
            segment = struct.unpack_from('<II16sQQQQiiII', raw, offset)
            for i in range(segment[9]):
                section = struct.unpack_from('<16s16sQQIIIIIIII', raw, offset + 72 + i * 80)
                if section[0].rstrip(b'\0') == b'__text' and section[1].rstrip(b'\0') == b'__TEXT':
                    return raw[section[4]:section[4] + section[3]]
        offset += size
    raise AssertionError('No native text section')


def main():
    checks = []
    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)
    for folder in [BASE, ROUND]:
        check(str(folder.name) + ' all archived hashes', all(sha(folder / p) == h for p, h in load(folder / 'manifest.json').items()))
        check(str(folder.name) + ' successful study', load(folder / 'summary.json')['status'] == 'pass' and not (folder / 'failure.json').exists())
    check('pinned Fossil source unchanged', all(sha(WORK / 'source-1573b8e6' / p) == h for p, h in load(WORK / 'source-manifest.json').items()))
    check('baseline executed binaries unchanged', all(sha(ROOT / p) == h for p, h in load(BASE / 'binary-hashes.json').items()))
    original = WORK / 'build-original/fossil'
    stripped = REC / 'fossil-symbols'
    check('debug stripping preserves all executable text bytes', text_section(original) == text_section(stripped))
    recovery = load(REC / 'functions/recovery.json')
    check('Ghidra imported the exact text-preserving binary', recovery['binary_sha256'] == sha(stripped))
    check('ten native functions recovered from binary', len(recovery['functions']) == 10 and all(x['completed'] for x in recovery['functions']) and recovery['source_files_used'] is False)
    registration = (REC / 'functions/timeline_query_for_tty.c').read_text()
    check('indirect callback registration recovered', '_sqlite3_create_function' in registration and '_wiki_to_text_sqlfunc' in registration)
    for size in [100, 2000]:
        before = load(BASE / str(size) / 'database-state.json')
        check(f'{size} full original repository', state(BASE / str(size) / 'repository.fossil') == before)
        for variant in ['default', 'full', 'hash']:
            expected = (BASE / f'{size}-{variant}-warmup.stdout').read_bytes()
            for suffix in ['plain-0', 'plain-1', 'sqltrace'] + (['profile'] if size == 2000 else []):
                check(f'{size}-{variant}-{suffix} exact output', (BASE / f'{size}-{variant}-{suffix}.stdout').read_bytes() == expected)
            if size == 2000:
                profile = load(BASE / f'2000-{variant}-profile.json')
                row = next(x for x in profile if x['function'] == 'wiki_to_text_sqlfunc')
                check(variant + ' converts all 2000 rows', row['calls'] == 2000)
    summary = load(ROUND / 'summary.json')
    check('20 complete roundtrip executions', summary['whole_application_executions'] == 20 and len(summary['whole_application_cases']) == 10)
    for case in summary['whole_application_cases']:
        size, variant = case['size'], case['variant']
        outputs = []
        for arm in ['original', 'roundtrip']:
            name = f'{size}-{variant}-{arm}'
            check(name + ' full database and schema', state(ROUND / name / 'repository.fossil') == load(BASE / str(size) / 'database-state.json'))
            outputs.append((ROUND / (name + '.stdout')).read_bytes())
        check(f'{size}-{variant} exact cross-arm bytes', outputs[0] == outputs[1])
    for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
        check(name + ' first-project core unchanged', sha(ROOT / 'scripts' / name) == sha(ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts' / name))
    check('native optimizer not falsely admitted', load(ROUND / 'common-decision.json')['decision']['status'] == 'unbound_description_no_plan')
    check('old continuation freeze untouched', all(sha(ROOT / p) == h for p, h in load(ROOT / 'paper/continuation-freeze.json')['sha256'].items()))
    (REC / 'inputs').mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'prototype/fossil/ghidra/RecoverFossil.java', REC / 'inputs/RecoverFossil.java')
    save(REC / 'binary-binding.json', dict(original_sha256=sha(original), analyzed_sha256=sha(stripped),
        identical_text_sha256=hashlib.sha256(text_section(original)).hexdigest(),
        ghidra_version='12.2', native_decompiler_built_for='mac_arm_64',
        ghidra_archive_sha256=sha(WORK / 'downloads/ghidra_12.2_PUBLIC_20261016.zip'),
        native_decompiler_sha256=sha(WORK / 'tools/ghidra_12.2_PUBLIC/Ghidra/Features/Decompiler/os/mac_arm_64/decompile'),
        source_input_to_decompiler=False, symbol_assistance=True, debug_information_removed=True))
    save(REC / 'manifest.json', {str(p.relative_to(REC)): sha(p) for p in sorted(REC.rglob('*')) if p.is_file() and p.name != 'manifest.json'})
    target = ROOT / 'evidence/fossil-trial-20261110-audit.json'
    if target.exists(): raise FileExistsError(target)
    save(target, dict(status='pass', checks=checks, checks_passed=len(checks), application_reruns=0,
        audit_script_sha256=sha(Path(__file__)), evidence_manifests={str(f.relative_to(ROOT)):sha(f / 'manifest.json') for f in [BASE, REC, ROUND]}))
    print(json.dumps(dict(status='pass', checks_passed=len(checks), report=str(target))))


if __name__ == '__main__': main()
