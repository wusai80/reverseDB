#!/usr/bin/env python3
"""Offline verification of retained scalar-contract evidence; no application run."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]).resolve()
checks = []
def read(name):
    return json.loads((out / name).read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def check(name, ok):
    assert ok, name
    checks.append(name)

manifest = read('manifest.json')
check('all raw artifacts intact', all(sha(out / p) == h for p, h in manifest.items()))
check('source and core unchanged', all(sha(ROOT / p) == h for p, h in read('inputs.json').items()))
reference = ROOT / 'evidence/fusion-placement/20261105-ea883b4b7dce/inputs/scripts'
for name in ['fusion_placement.py', 'fusion_regions.py', 'fusion_placement_contracts.py']:
    check(name + ' equals shared first-project version', sha(ROOT / 'scripts' / name) == sha(reference / name))
protocol, report, cases = read('protocol.json'), read('report.json'), read('cases.json')
check('fixed schedule, seed, no timings', protocol['cases'] == [c['case'] for c in cases]
      and protocol['seed'] == '20261108' and protocol['timings'] is False)
check('60 local executions', len(cases) == 20 and sum(len(c['arms']) for c in cases) == 60)
expected = {'default': 17, 'reverse': 109, 'ties': 102, 'null': 0, 'numeric-text': 18,
            'invalid-number': 0, 'multidisc': 10, 'flex-sort': 106, 'converter-effect': 17,
            'concurrent-fixed': 17}
for case in cases:
    name, arms = case['case'], case['arms']
    if name in expected:
        check(name + ' known oracle values and types', all(a['outcome'] == dict(value=expected[name], type='int') for a in arms))
    before, after = [], []
    for arm in arms:
        prefix = name + '/' + arm['arm']
        before.append(read(prefix + '/before.json'))
        after.append(read(prefix + '/after.json'))
        with sqlite3.connect(out / prefix / 'library.db') as conn:
            conn.text_factory = bytes
            schema = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            tables = {}
            for (raw,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                table = raw.decode()
                tables[table] = sorted(repr(r) for r in conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
            state = dict(schema=[repr(r) for r in schema], tables=tables)
            check(prefix + ' retained real database matches full snapshot', state == after[-1] and len(tables) == 5)
            if name.startswith('concurrent-'):
                check(prefix + ' external writes actually persisted', conn.execute('SELECT DISTINCT tracktotal FROM items WHERE album_id=1').fetchall() == [(29,)])
    check(name + ' identical full starting and final states across arms', before[0] == before[1] == before[2] and after[0] == after[1] == after[2])
    if not name.startswith('concurrent-'):
        check(name + ' complete state unchanged', before == after)
    check(name + ' retain-read identical query stream', arms[0]['queries'] == arms[2]['queries'])
    if name in ['multidisc', 'flex-sort', 'deny-fixed']:
        check(name + ' drop arm retained whole query stream', arms[0]['queries'] == arms[1]['queries'])
    else:
        check(name + ' drop arm removes only original second flex read', arms[1]['queries'] == arms[0]['queries'][:1]
              and len(arms[0]['queries']) == 2 and 'FROM item_attributes' in arms[0]['queries'][1]['sql'])
    for arm in arms[1:]:
        equal = all(arm[k] == arms[0][k] for k in ['outcome', 'events', 'escaped_objects'])
        check(name + '/' + arm['arm'] + ' equality recomputed', equal == case['equal'][arm['arm']])

for arm, key in [('drop-flex-and-model', 'expected_drop_counterexamples'), ('retain-flex-drop-model', 'expected_keep_counterexamples')]:
    check(arm + ' expected negative controls', sorted(c['case'] for c in cases if not c['equal'][arm]) == protocol[key])
exports = report['exports']
a = (out / 'export/original/output.json').read_bytes()
b = (out / 'export/drop-flex-and-model/output.json').read_bytes()
check('full export byte equality, 100 rows', a == b and len(json.loads(a)) == 100)
check('all exported flex and media fields retained', all({'audit_note', 'probe_tag', 'rating_hint', 'filesize', 'has_cover_art'} <= r.keys() for r in json.loads(a)))
original = read('export/original/queries.json')
candidate = read('export/drop-flex-and-model/queries.json')
position, removed = 0, []
for query in original:
    if position < len(candidate) and query == candidate[position]:
        position += 1
    else:
        removed.append(query)
check('exact 90 flex reads removed, all remaining SQL/params ordered-identical', position == len(candidate)
      and len(original) == 362 and len(candidate) == 272 and len(removed) == 90
      and all('FROM item_attributes' in r['sql'] and 'album_id' in r['sql'] for r in removed))
for arm in ['original', 'drop-flex-and-model']:
    check(arm + ' full export state unchanged', read('export/' + arm + '/before.json') == read('export/' + arm + '/after.json'))
check('full export state equal across arms', read('export/original/after.json') == read('export/drop-flex-and-model/after.json'))
check('no admission or speed claim', report['unrestricted_candidate'] == 'rejected' and not report['backend_admitted'] and not report['speedup_measured'])
audit = dict(status='passed', checks=checks, raw_artifacts=len(manifest),
             raw_report_sha256=sha(out / 'report.json'), audit_source_sha256=sha(Path(__file__)),
             scope='Offline evidence verification; no rerun, timing, optimizer invocation or new capability proof')
target = out.with_name(out.name + '-audit.json')
target.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(dict(audit=str(target), checks=len(checks), raw_artifacts=len(manifest))))
