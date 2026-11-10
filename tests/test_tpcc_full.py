"""Full five-transaction candidate: evidence isolation and failure controls."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/tpcc-full/20261103-b7157edda001/run'
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_app import compare_app


class FullAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.left = EVIDENCE / 'transaction_edges_reference'
        self.right = Path(self.temp.name) / 'candidate'
        shutil.copytree(EVIDENCE / 'transaction_edges_candidate', self.right)

    def change(self, filename, mutate):
        path = self.right / filename
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_all_four_scenarios_match(self):
        for scenario in ['baseline', 'holdout', 'transaction_edges', 'delivery_edges']:
            compare_app(EVIDENCE / (scenario + '_reference'), EVIDENCE / (scenario + '_candidate'))

    def test_actual_candidate_processes_exclude_reference(self):
        report = json.loads((EVIDENCE / 'run.json').read_text())
        commands = [c for c in report['commands'] if 'reversedb.tpcc.CandidateApp' in c]
        self.assertEqual(len(commands), 4)
        for command in commands:
            paths = command[command.index('-cp') + 1].split(':')
            self.assertEqual([Path(p).name for p in paths],
                             ['standalone-candidate', 'json-20240303.jar', 'postgresql-42.7.4.jar'])

    def test_bad_credit_data_change_rejected(self):
        self.change('observations.json', lambda data: data[0]['after']['customer'][0].update(c_data='lost history'))
        with self.assertRaisesRegex(ValueError, 'observations'):
            compare_app(self.left, self.right)

    def test_delivery_balance_change_rejected(self):
        def mutate(data):
            row = next(x for x in data if 'queue' in x['after'])['after']['orders'][0]['customer'][0]
            row['c_balance'] += 1
        self.change('observations.json', mutate)
        with self.assertRaisesRegex(ValueError, 'observations'):
            compare_app(self.left, self.right)

    def test_wrong_stock_count_rejected(self):
        def mutate(data):
            row = next(x for x in data if 'low_stock_count' in x.get('result', {}))
            row['result']['low_stock_count'] += 1
        self.change('responses.json', mutate)
        with self.assertRaisesRegex(ValueError, 'responses'):
            compare_app(self.left, self.right)

    def test_wrong_latest_order_rejected(self):
        self.change('responses.json', lambda data: data[17]['result']['order'].update(o_id=3000))
        with self.assertRaisesRegex(ValueError, 'responses'):
            compare_app(self.left, self.right)

    def test_missing_step_observations_rejected(self):
        (self.right / 'observations.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            compare_app(self.left, self.right)

    def test_read_only_queries_marked_and_server_enforced(self):
        data = json.loads((self.right / 'observations.json').read_text())
        self.assertEqual(sum(x['read_only'] for x in data), 5)
        self.assertIn('READ ONLY', (EVIDENCE / 'postgres.log').read_text())


if __name__ == '__main__':
    unittest.main()
