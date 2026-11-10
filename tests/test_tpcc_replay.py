"""Reject altered ordering, parameters, read results and row state in real replay evidence."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_replay import compare


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.left = ROOT / 'evidence/tpcc-replay/20261103-7e3eaafec61d/replay_a'
        self.right = Path(self.temp.name) / 'replay_b'
        shutil.copytree(self.left.parent / 'replay_b', self.right)

    def mutate(self, filename, change):
        path = self.right / filename
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, filename):
            compare(self.left, self.right)

    def test_real_replays_equal(self):
        compare(self.left, self.right)

    def test_transaction_order_change(self):
        def change(events):
            events[0], events[1] = events[1], events[0]
        self.mutate('transactions.json', change)

    def test_query_order_change(self):
        def change(events):
            trace = events[0]['trace']
            indices = [i for i, e in enumerate(trace) if e['op'] == 'executeQuery']
            a, b = indices[:2]
            trace[a], trace[b] = trace[b], trace[a]
        self.mutate('transactions.json', change)

    def test_bound_parameter_change(self):
        def change(events):
            event = next(e for e in events[0]['trace'] if e['op'] == 'setInt')
            event['args'][1] += 1
        self.mutate('transactions.json', change)

    def test_time_is_not_ignored(self):
        def change(events):
            event = next(e for e in events[0]['trace'] if e['op'] == 'setTimestamp')
            event['args'][1] = '2000-01-01 00:00:00.0'
        self.mutate('transactions.json', change)

    def test_read_value_change(self):
        def change(events):
            event = next(e for e in events[0]['trace'] if e['op'] == 'getFloat')
            event['value'] += 1
        self.mutate('transactions.json', change)

    def test_database_state_change(self):
        self.mutate('final-state.json', lambda data: data['stock'].update(sha256='changed'))


if __name__ == '__main__':
    unittest.main()
