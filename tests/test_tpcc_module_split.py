"""The module-boundary audit must reject timing changes even if end-state files match."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/tpcc-module-split/20261103-5d3d0db9587a'
sys.path.insert(0, str(ROOT / 'scripts'))
from tpcc_module_split import assess


class ModuleSplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = EVIDENCE / 'control/custom_candidate'
        self.split = Path(self.temp.name) / 'split'
        shutil.copytree(EVIDENCE / 'split/custom_candidate', self.split)
        self.commands = json.loads((EVIDENCE / 'commands.json').read_text())['commands']

    def mutate(self, fn):
        path = self.split / 'jdbc-trace.json'
        value = json.loads(path.read_text())
        fn(value[0]['events'])
        path.write_text(json.dumps(value))

    def test_observed_split_matches(self):
        self.assertEqual(assess(self.control, self.split, self.commands)['cases'], 11)

    def test_module_committing_early_is_rejected(self):
        self.mutate(lambda events: events.insert(0, {'op': 'commit'}))
        with self.assertRaisesRegex(ValueError, 'transaction boundaries changed'):
            assess(self.control, self.split, self.commands)

    def test_stock_apply_before_order_lines_is_rejected_for_this_contract(self):
        def swap(events):
            indices = [i for i,e in enumerate(events) if e['op'] == 'executeBatch']
            self.assertEqual(len(indices), 2)
            a,b=indices
            events[a],events[b]=events[b],events[a]
        self.mutate(swap)
        with self.assertRaisesRegex(ValueError, 'transaction boundaries changed'):
            assess(self.control, self.split, self.commands)


if __name__ == '__main__':
    unittest.main()
