"""Negative controls against frozen real execution evidence; no database needed."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from verify_tpcc_baseline import verify


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        frozen = ROOT / 'evidence/tpcc/20261103-c615d3357989/run'
        for file in frozen.iterdir():
            if file.is_file() and file.suffix in {'.json', '.log'}:
                shutil.copy2(file, self.path / file.name)

    def change(self, name, mutate):
        path = self.path / name
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_observed_run_passes(self):
        self.assertEqual(verify(self.path)['status'], 'passed')

    def test_missing_type_rejected_even_with_consistent_total(self):
        def remove(data):
            hist = data['completed']
            hist['NUM_SAMPLES'] -= hist['HISTOGRAM'].pop(next(iter(hist['HISTOGRAM'])))
        self.change('histograms.json', remove)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            verify(self.path)

    def test_missing_invariant_rejected(self):
        self.change('invariants-after-workload.json', lambda d: d.pop('new_order_gaps'))
        with self.assertRaisesRegex(ValueError, 'Invariant'):
            verify(self.path)

    def test_leaked_resource_rejected(self):
        self.change('run.json', lambda d: d['remaining'].update(volume='unremoved-volume'))
        with self.assertRaisesRegex(ValueError, 'Cleanup'):
            verify(self.path)

    def test_unexplained_database_error_rejected(self):
        with (self.path / 'postgres.log').open('a') as stream:
            stream.write('\nERROR: connection lost\n')
        with self.assertRaisesRegex(ValueError, 'Unexplained'):
            verify(self.path)

    def test_missing_commit_evidence_rejected(self):
        path = self.path / 'postgres.log'
        lines = path.read_text().splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines)
                     if 'app=tpcc-functional ' in line and line.endswith(': COMMIT\n'))
        lines[index] = lines[index].replace(': COMMIT\n', ': MISSING\n')
        path.write_text(''.join(lines))
        with self.assertRaisesRegex(ValueError, 'Unclosed|Commit'):
            verify(self.path)


if __name__ == '__main__':
    unittest.main()
