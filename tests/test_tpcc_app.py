"""Business comparison controls on real reference/candidate evidence."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_app import compare_app
from materialize_tpcc_commands import materialize, SOURCE


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = ROOT / 'evidence/tpcc-app/20261103-e8bad4010a24/run'
        self.left = base / 'holdout_reference'
        self.right = Path(self.temp.name) / 'candidate'
        shutil.copytree(base / 'holdout_candidate', self.right)

    def change(self, name, mutate):
        path = self.right / name
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_real_independent_neworder_matches(self):
        compare_app(self.left, self.right)
        self.assertNotEqual(json.loads((self.left / 'jdbc-trace.json').read_text()),
                            json.loads((self.right / 'jdbc-trace.json').read_text()))

    def test_recorded_inputs_materialize_reproducibly(self):
        expected = json.loads((ROOT / 'app/tpcc/scenarios/baseline.json').read_text())
        self.assertEqual(materialize(json.loads(SOURCE.read_text())), expected)

    def test_wrong_order_id_rejected(self):
        self.change('responses.json', lambda responses: responses[0]['result']['order'].update(o_id=999))
        with self.assertRaisesRegex(ValueError, 'responses'):
            compare_app(self.left, self.right)

    def test_partial_rollback_rejected(self):
        def mutate(responses):
            aborted = next(r for r in responses if r['status'] == 'aborted')
            aborted['state']['district'][0]['d_next_o_id'] += 1
        self.change('responses.json', mutate)
        with self.assertRaisesRegex(ValueError, 'responses'):
            compare_app(self.left, self.right)

    def test_hidden_database_change_rejected(self):
        self.change('final-state.json', lambda state: state['customer'].update(sha256='changed'))
        with self.assertRaisesRegex(ValueError, 'final-state'):
            compare_app(self.left, self.right)

    def test_rejected_input_cannot_be_marked_committed(self):
        def mutate(responses):
            next(r for r in responses if r['status'] == 'rejected')['status'] = 'committed'
        self.change('responses.json', mutate)
        with self.assertRaisesRegex(ValueError, 'responses'):
            compare_app(self.left, self.right)


if __name__ == '__main__':
    unittest.main()
