"""Negative controls for reconstructed artifacts and JVM load provenance."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/tpcc-roundtrip/20261103-9954559e225c'
sys.path.insert(0, str(ROOT / 'scripts'))
from rebuild_tpcc import validate_bundle, verify_loaded_classes
from tpcc_app import compare_app


class RebuildControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        shutil.copytree(EVIDENCE / 'bundle', self.bundle)
        # Artifact checks are portable; substitute one local dependency for external JARs.
        dependency = self.root / 'dependency.jar'
        dependency.write_bytes(b'test dependency')
        self.manifest = json.loads((self.bundle / 'build.json').read_text())
        self.manifest['dependencies'] = {str(dependency): hashlib.sha256(dependency.read_bytes()).hexdigest()}
        (self.bundle / 'build.json').write_text(json.dumps(self.manifest))
        self.run = self.root / 'run'; self.run.mkdir()
        (self.run / 'run.json').write_text(json.dumps({'cases': {'test_reference': {}, 'test_candidate': {}}}))
        for engine in ['reference', 'candidate']:
            folder = self.run / ('test_' + engine); folder.mkdir()
            origin = self.bundle / ('reference.jar' if engine == 'reference' else 'candidate-classes')
            lines = [f"[info][class,load] {row['member'][:-6].replace('/', '.')} source: {origin.as_uri()}"
                     for row in self.manifest['rebuilt_classes'] if row['engine'] == engine]
            (folder / 'class-load.log').write_text('\n'.join(lines))

    def test_bundle_and_complete_loading_pass(self):
        self.assertEqual(verify_loaded_classes(self.run, self.bundle)['class_count'], 16)

    def test_business_bytecode_change_rejected(self):
        path = self.bundle / 'candidate-classes/reversedb/tpcc/PaymentService.class'
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'runtime file changed'):
            validate_bundle(self.bundle)

    def test_extra_runtime_class_rejected(self):
        (self.bundle / 'candidate-classes/Unexpected.class').write_bytes(b'fallback')
        with self.assertRaisesRegex(ValueError, 'Unexpected runtime classes'):
            validate_bundle(self.bundle)

    def test_source_change_rejected(self):
        path = self.bundle / next(iter(self.manifest['sources']))
        path.write_text(path.read_text() + '\n// edited')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            validate_bundle(self.bundle)

    def test_dependency_change_rejected(self):
        (self.root / 'dependency.jar').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            validate_bundle(self.bundle)

    def test_wrong_origin_with_matching_prefix_rejected(self):
        path = self.run / 'test_candidate/class-load.log'
        path.write_text(path.read_text().replace('candidate-classes', 'candidate-classes-original'))
        with self.assertRaisesRegex(ValueError, 'Wrong class origin'):
            verify_loaded_classes(self.run, self.bundle)

    def test_missing_class_load_rejected(self):
        path = self.run / 'test_candidate/class-load.log'
        path.write_text('\n'.join(path.read_text().splitlines()[1:]))
        with self.assertRaisesRegex(ValueError, 'never loaded'):
            verify_loaded_classes(self.run, self.bundle)

    def test_partial_run_reports_only_observed_classes(self):
        (self.run / 'test_reference/class-load.log').write_text('')
        binding = verify_loaded_classes(self.run, self.bundle, require_all=False)
        self.assertEqual(binding['class_count'], 10)


class RoundtripEvidence(unittest.TestCase):
    def test_all_rebuilt_cases_match_original_individually(self):
        run = EVIDENCE / 'run'
        record = json.loads((run / 'run.json').read_text())
        self.assertEqual(record['status'], 'passed')
        self.assertFalse(record['cleanup_errors'])
        self.assertFalse(any(record['remaining'].values()))
        self.assertEqual(len(record['cases']), 8)
        frozen = ROOT / 'evidence/tpcc-full/20261103-b7157edda001/run'
        for case in record['cases']:
            compare_app(frozen / case, run / case)
        binding = json.loads((run / 'rebuild-class-binding.json').read_text())
        self.assertEqual(binding['class_count'], 16)

    def test_actual_runtime_commands_use_rebuilt_artifacts(self):
        record = json.loads((EVIDENCE / 'run/run.json').read_text())
        commands = [c for c in record['commands'] if c[0] == 'java']
        self.assertEqual(len(commands), 8)
        for command in commands:
            paths = command[command.index('-cp') + 1].split(':')
            names = [Path(p).name for p in paths]
            if 'reversedb.tpcc.CandidateApp' in command:
                self.assertEqual(names, ['candidate-classes', 'json-20240303.jar', 'postgresql-42.7.4.jar'])
            else:
                self.assertEqual(names, ['reference-adapter', 'candidate-classes', 'reference.jar', '*'])


if __name__ == '__main__':
    unittest.main()
