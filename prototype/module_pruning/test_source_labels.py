import unittest

from source_labels import build_mutant, decide_label, proof_for_contract


STRATEGY = '''
class BulkArchiveStrategy:
    def __init__(self, zipf, *, follow_formatting=False):
        self.zipf = zipf
        if follow_formatting:
            self.make_unique_filename = self._formatted_filepath
        else:
            self.make_unique_filename = self._filename_only
    def _formatted_filepath(self):
        return "formatted"
    def _filename_only(self):
        return "plain"
'''


class SourceLabelTest(unittest.TestCase):
    def test_passing_examples_without_argument_are_unknown(self):
        pairs = [dict(equal=True,original={"error":None},deleted={"error":None})]
        self.assertIsNone(decide_label(pairs,{"established":False})["label"])
        self.assertEqual(decide_label(pairs,{"established":True})["label"],0)
        self.assertIsNone(decide_label([],{"established":True})["label"])

    def test_witness_requires_successful_original(self):
        pair = dict(equal=False,original={"error":None,"value":1},deleted={"error":None,"value":2})
        self.assertEqual(decide_label([pair],{"established":False})["label"],1)
        pair["original"]["error"] = {"type":"HarnessError"}
        self.assertIsNone(decide_label([pair],{"established":False})["label"])

    def test_switching_capability_invalidates_removability_argument(self):
        mutant = build_mutant(STRATEGY,"paperless-ngx","formatted")
        disabled = dict(project="paperless-ngx",follow_formatting=False)
        self.assertTrue(proof_for_contract(disabled,mutant)["established"])
        self.assertFalse(proof_for_contract(dict(disabled,follow_formatting=True),mutant)["established"])
        self.assertIn("def _filename_only",mutant["mutant_source"])
        self.assertNotIn("def _formatted_filepath",mutant["mutant_source"])

    def test_extra_consumer_rejects_unreachability_rule(self):
        changed = STRATEGY+'\n    def another_consumer(self):\n        return self._formatted_filepath()\n'
        with self.assertRaises(ValueError):
            build_mutant(changed,"paperless-ngx","formatted")

    def test_changed_price_guard_or_dataclass_fails_closed(self):
        source = '''
def _append_line_to_update(line_data, line):
    if line_data.custom_price_to_update:
        line.price = line_data.custom_price
    return line
'''
        mutant = build_mutant(source,"saleor","price")
        dto = '@dataclass\nclass CheckoutLineData:\n    custom_price_to_update: bool = False\n'
        case = dict(project="saleor",contract="disabled")
        self.assertTrue(proof_for_contract(case,mutant,dto)["established"])
        self.assertFalse(proof_for_contract(dict(case,contract="enabled"),mutant,dto)["established"])
        with self.assertRaises(ValueError):
            build_mutant(source.replace('if line_data.custom_price_to_update:', 'if line_data.custom_price_to_update or line.quantity:'),"saleor","price")
        with self.assertRaises(ValueError):
            proof_for_contract(case,mutant,dto.replace('bool = False','bool = True'))


if __name__ == "__main__":
    unittest.main()
