"""Negative controls for command/JDBC/server-log correlation on real monitored evidence."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/tpcc-observation/20261103-8729d2283fdc'
sys.path.insert(0, str(ROOT / 'scripts'))
from summarize_tpcc_observation import summarize
from decompile_tpcc import checks_passed


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'run'
        shutil.copytree(EVIDENCE / 'run', self.path,
                        ignore=shutil.ignore_patterns('*.jfr', 'inputs', 'classes', 'standalone-candidate'))

    def change(self, name, mutate):
        p = self.path / name
        value = json.loads(p.read_text())
        mutate(value)
        p.write_text(json.dumps(value))

    def test_observed_run_correlates(self):
        result = summarize(self.path)
        self.assertEqual(result['correlated_commands'], 108)

    def test_missing_transaction_tag_rejected(self):
        p = self.path / 'postgres.log'
        p.write_text(p.read_text().replace('app=rdb-t1-s1 db=baseline_reference',
                                         'app=lost-tag db=baseline_reference'))
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'):
            summarize(self.path)

    def test_wrong_commit_status_rejected(self):
        self.change('baseline_reference/runtime.json', lambda events: events[0].update(status='aborted'))
        with self.assertRaisesRegex(ValueError, 'boundary/status'):
            summarize(self.path)

    def test_missing_timing_rejected(self):
        def mutate(traces):
            next(e for e in traces[0]['events'] if e['op'].startswith('execute')).pop('duration_ns')
        self.change('baseline_reference/jdbc-trace.json', mutate)
        with self.assertRaisesRegex(ValueError, 'timing'):
            summarize(self.path)

    def test_missing_command_rejected(self):
        self.change('baseline_reference/runtime.json', lambda events: events.pop(0))
        with self.assertRaisesRegex(ValueError, 'runtime command'):
            summarize(self.path)


class DecompilationChecks(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EVIDENCE / 'decompilation/manifest.json').read_text())

    def test_actual_decompilation_checks_pass(self):
        self.assertTrue(checks_passed(self.manifest))

    def test_javac_failure_rejected(self):
        self.manifest['outcomes']['candidate']['recompile_returncode'] = 1
        self.assertFalse(checks_passed(self.manifest))

    def test_empty_output_rejected(self):
        self.manifest['outcomes']['reference']['java_sources'] = []
        self.assertFalse(checks_passed(self.manifest))


if __name__ == '__main__':
    unittest.main()
