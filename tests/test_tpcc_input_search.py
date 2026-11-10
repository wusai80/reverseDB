"""Generic input-search checks, without embedding the target stock rule."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_input_search import generate, search_trace


class InputSearchTests(unittest.TestCase):
    def setUp(self):
        self.commands = [{'id': 'a'}, {'id': 'b'}]
        rows = [{'id': x, 'status': 'committed', 'result': {'ok': True}} for x in ['a', 'b']]
        observations = [{'id': x, 'before': {'customer': {'balance': 42}}, 'after': {'customer': {'balance': 42}}} for x in ['a', 'b']]
        self.responses = [copy.deepcopy(rows), copy.deepcopy(rows)]
        self.observations = [copy.deepcopy(observations), copy.deepcopy(observations)]

    def run_search(self):
        return search_trace(self.commands, self.responses, self.observations)

    def test_detects_unrelated_state_field_without_rule_model(self):
        self.observations[1][1]['after']['customer']['balance'] = 43
        result = self.run_search()
        self.assertEqual(result['first_step'], 2)
        self.assertEqual(result['state_differences'], [{'path': '/after/customer/balance', 'control': 42, 'mutant': 43}])
        self.assertEqual(result['witness'], self.commands)

    def test_detects_result_difference(self):
        self.responses[1][0]['result']['ok'] = False
        self.assertEqual(self.run_search()['first_step'], 1)

    def test_no_difference_is_only_bounded_not_found(self):
        result = self.run_search()
        self.assertEqual(result['outcome'], 'not_found')
        self.assertNotIn('witness', result)

    def test_rejects_different_pre_states(self):
        self.observations[1][0]['before']['customer']['balance'] = 0
        with self.assertRaisesRegex(ValueError, 'Pre-state'):
            self.run_search()

    def test_rejects_missing_observation(self):
        self.observations[1].pop()
        with self.assertRaisesRegex(ValueError, 'Missing/reordered observations'):
            self.run_search()

    def test_rejects_execution_failure_as_counterexample(self):
        self.responses[1][0]['status'] = 'aborted'
        with self.assertRaisesRegex(ValueError, 'execution failure'):
            self.run_search()

    def test_generation_reproducible_and_within_contract(self):
        a = generate(20261103, 6)
        self.assertEqual(a, generate(20261103, 6))
        for command in a['commands']:
            self.assertTrue(all(1 <= line['quantity'] <= 10 for line in command['lines']))
        self.assertEqual(len({c['id'] for c in a['commands']}), 6)


if __name__ == '__main__':
    unittest.main()
