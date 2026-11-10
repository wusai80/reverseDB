import ast
from pathlib import Path
import unittest

from effect_features import extract


class EffectsTest(unittest.TestCase):
    def setUp(self):
        self.source=Path(__file__).with_name("calibration_suite.py").read_text()

    def features(self,family,variant,source=None,values=None):
        return extract(self.source if source is None else source,{"family":family,"v":variant},
                       {"x":[-1,0,1] if values is None else values,"flag":[False,True]})["features"]

    def test_visible_and_scratch_writes(self):
        self.assertTrue(self.features("persistent_state",0)["observable_write"])
        self.assertFalse(self.features("persistent_state",1)["observable_write"])

    def test_alias_and_snapshot_copy_differ(self):
        self.assertTrue(self.features("heap_alias",0)["alias_write_to_observation"])
        self.assertFalse(self.features("heap_alias",1)["observable_write"])
        changed=self.source.replace("view=dict(cell) if v==1 else cell","view=cell")
        self.assertTrue(self.features("heap_alias",1,changed)["alias_write_to_observation"])

    def test_contract_singleton_suppresses_unreachable_raise(self):
        self.assertTrue(self.features("exception_control",1)["module_may_raise"])
        self.assertFalse(self.features("exception_control",1,values=[0])["module_may_raise"])

    def test_zero_update_and_identity_assignment(self):
        self.assertTrue(self.features("persistent_state",2)["neutral_update_only"])
        self.assertTrue(self.features("returned_value",1)["neutral_update_only"])
        changed=self.source.replace("slot=0\n","slot=7\n",1)
        self.assertFalse(self.features("returned_value",1,changed)["neutral_update_only"])

    def test_retained_actions(self):
        self.assertTrue(self.features("authorization_guard",1)["retained_similar_action"])
        self.assertTrue(self.features("resource_lifetime",1)["retained_similar_action"])
        self.assertFalse(self.features("authorization_guard",0)["retained_similar_action"])

    def test_parameter_and_label_injection_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.source,{"family":"heap_alias","v":0,"label":1},{"x":[0],"flag":[True]})
        with self.assertRaises(ValueError):
            extract(self.source,{"family":"heap_alias","v":0},{"x":[0],"flag":[True],"counterexample":[]})

    def test_local_renaming_does_not_change_features(self):
        class Rename(ast.NodeTransformer):
            def visit_Name(self,node):
                if node.id in {"balance","scratch","cell","view","handle","observable","slot","value","denied"}:
                    node.id="renamed_"+node.id
                return node
        renamed=ast.unparse(Rename().visit(ast.parse(self.source)))
        for family in ["returned_value","persistent_state","exception_control","heap_alias","authorization_guard","resource_lifetime"]:
            for variant in range(4):
                self.assertEqual(self.features(family,variant),self.features(family,variant,renamed))

    def test_oracle_function_changes_do_not_change_features(self):
        changed=self.source.replace('"label":int(witness is not None)','"label":0')
        self.assertNotEqual(changed,self.source)
        self.assertEqual(self.features("heap_alias",0),self.features("heap_alias",0,changed))


if __name__=="__main__": unittest.main()
