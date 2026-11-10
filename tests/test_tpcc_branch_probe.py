"""Reject false positive conclusions in the one-branch intervention experiment."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/tpcc-branch-probe/20261103-44307411537f'
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_branch_probe import assess


class BranchProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = EVIDENCE / 'control/custom_candidate'
        self.mutant = Path(self.temp.name) / 'mutant'
        shutil.copytree(EVIDENCE / 'mutant-run/custom_candidate', self.mutant)

    def change(self, filename, mutate):
        path = self.mutant / filename
        value = json.loads(path.read_text())
        mutate(value)
        path.write_text(json.dumps(value))

    def test_real_experiment_detects_only_active_branch(self):
        result = assess(self.control, self.mutant)
        self.assertEqual(result['first_difference'], 'below-9')
        self.assertTrue(result['order_results_equal'])

    def test_inactive_control_change_rejected(self):
        def mutate(rows):
            rows[0]['after']['stock'][0]['s_quantity'] += 1
        self.change('observations.json', mutate)
        with self.assertRaisesRegex(ValueError, 'Inactive controls'):
            assess(self.control, self.mutant)

    def test_ineffective_mutation_rejected(self):
        def mutate(rows):
            next(r for r in rows[3]['after']['stock'] if r['s_i_id'] == 4)['s_quantity'] = 100
        self.change('observations.json', mutate)
        with self.assertRaisesRegex(ValueError, 'Unexpected post-stock'):
            assess(self.control, self.mutant)

    def test_collateral_change_rejected(self):
        def mutate(rows):
            rows[3]['after']['district'][0]['d_tax'] = 0
        self.change('observations.json', mutate)
        with self.assertRaisesRegex(ValueError, 'collateral'):
            assess(self.control, self.mutant)


if __name__ == '__main__':
    unittest.main()
