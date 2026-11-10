import ast
import textwrap
import unittest

from control_features import extract_control


DOMAINS = {"x": [-2,-1,0,1,2], "flag": [False,True]}


def source(body):
    return ('class Rejected(Exception):\n    pass\n\n'
            'def execute(x, flag, removed):\n    try:\n'
            + textwrap.indent(textwrap.dedent(body).strip(), '        ')
            + '\n    except Rejected as error:\n        return {"error": str(error)}\n')


class ControlFeaturesTest(unittest.TestCase):
    def facts(self, body):
        return extract_control(source(body), DOMAINS)

    def test_write_to_guard_to_exception_has_witness(self):
        facts = self.facts('''
            denied = False
            if not removed:
                denied = x < 0
            if denied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertTrue(facts["features"]["module_controls_observation"])
        witness = facts["control_paths"][0]
        self.assertEqual([e["kind"] for e in witness["edges"]], ["data", "control"])
        self.assertEqual(facts["nodes"][witness["observation_node"]]["kind"], "raise")

    def test_transitive_scalar_copy_to_guard(self):
        facts = self.facts('''
            denied = False
            if not removed:
                denied = x < 0
            copied = denied
            if copied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertTrue(facts["features"]["module_controls_observation"])
        self.assertEqual([e["kind"] for e in facts["control_paths"][0]["edges"]], ["data", "data", "control"])

    def test_guard_controls_returned_value_through_assignment(self):
        facts = self.facts('''
            selected = False
            amount = 0
            if not removed:
                selected = flag
            if selected:
                amount = x
            return {"amount": amount}
        ''')
        self.assertTrue(facts["features"]["module_controls_observation"])
        self.assertIn("control", [e["kind"] for e in facts["control_paths"][0]["edges"]])

    def test_scalar_overwrite_kills_definition(self):
        facts = self.facts('''
            denied = False
            if not removed:
                denied = x < 0
            denied = False
            if denied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertFalse(facts["features"]["module_controls_observation"])

    def test_conditional_overwrite_keeps_other_definition(self):
        facts = self.facts('''
            denied = False
            if not removed:
                denied = x < 0
            if flag:
                denied = False
            if denied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertTrue(facts["features"]["module_controls_observation"])

    def test_branch_on_dead_scratch_is_not_observation(self):
        facts = self.facts('''
            selected = False
            scratch = 0
            if not removed:
                selected = flag
            if selected:
                scratch = x
            return {"ok": True}
        ''')
        self.assertFalse(facts["features"]["module_controls_observation"])

    def test_module_early_return_is_effect(self):
        facts = self.facts('''
            if not removed:
                if x < 0:
                    return {"ok": False}
            return {"ok": True}
        ''')
        self.assertTrue(facts["features"]["module_may_return"])
        self.assertTrue(facts["features"]["module_controls_observation"])

    def test_unconditional_module_return_is_not_lost(self):
        facts = self.facts('''
            if not removed:
                return {"ok": False}
            return {"ok": True}
        ''')
        self.assertTrue(facts["features"]["module_may_return"])
        self.assertEqual(len([n for n in facts["nodes"] if n["kind"] == "return"]), 1)

    def test_early_exit_controls_continuation_outside_indent(self):
        facts = self.facts('''
            stop = False
            if not removed:
                stop = flag
            if stop:
                return {"ok": False}
            return {"ok": True}
        ''')
        returns = [n["id"] for n in facts["nodes"] if n["kind"] == "return"]
        guard = next(n["id"] for n in facts["nodes"] if n["kind"] == "branch")
        for observer in returns:
            self.assertIn(dict(source=guard, target=observer, kind="control"), facts["edges"])

    def test_sibling_branches_do_not_create_data_flow(self):
        facts = self.facts('''
            denied = False
            if flag:
                if not removed:
                    denied = x < 0
            else:
                if denied:
                    raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertFalse(facts["features"]["module_controls_observation"])

    def test_guard_context_distinguishes_same_action(self):
        body = '''
            if not removed:
                if x < 0:
                    raise Rejected("DENIED")
            if x < 0:
                raise Rejected("DENIED")
            return {"ok": True}
        '''
        same = self.facts(body)
        different = self.facts(body.replace('\n            if x < 0:', '\n            if x > 0:'))
        self.assertTrue(same["features"]["retained_action_same_guard_context"])
        self.assertFalse(different["features"]["retained_action_same_guard_context"])
        self.assertFalse(same["retained_guard_matches"][0]["replacement_proven"])

    def test_same_guard_name_after_reassignment_is_not_match(self):
        facts = self.facts('''
            denied = x < 0
            if not removed:
                if denied:
                    raise Rejected("DENIED")
            denied = x > 0
            if denied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertFalse(facts["features"]["retained_action_same_guard_context"])

    def test_implicit_exception_stays_unknown(self):
        facts = self.facts('''
            if not removed:
                scratch = 1 // x
            return {"ok": True}
        ''')
        self.assertIsNone(facts["features"]["module_controls_observation"])
        self.assertEqual(facts["status"], "partial")

    def test_augmented_division_also_stays_unknown(self):
        facts = self.facts('''
            scratch = 1
            if not removed:
                scratch //= x
            return {"ok": True}
        ''')
        self.assertIsNone(facts["features"]["module_controls_observation"])
        self.assertIn("implicit_arithmetic_exception_not_summarized", [r["reason"] for r in facts["unknown_effects"]])

    def test_expression_control_is_not_silently_absent(self):
        for expression in ['1 if selected else 0', 'selected and x']:
            facts = self.facts('''
                selected = False
                if not removed:
                    selected = flag
                return {"value": EXPRESSION}
            '''.replace('EXPRESSION', expression))
            self.assertIsNone(facts["features"]["module_controls_observation"])
            self.assertIn("expression_level_control_not_summarized", [r["reason"] for r in facts["unknown_effects"]])

    def test_conditionally_unbound_scalar_is_not_complete(self):
        facts = self.facts('''
            if flag:
                denied = False
            if not removed:
                scratch = x
            if denied:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertEqual(facts["status"], "partial")
        self.assertIn("conditionally_defined_scalar_may_be_unbound", [r["reason"] for r in facts["unknown_effects"]])

    def test_heap_alias_is_not_claimed_complete(self):
        facts = self.facts('''
            cell = {"flag": False}
            view = cell
            if not removed:
                cell["flag"] = flag
            if view["flag"]:
                raise Rejected("DENIED")
            return {"ok": True}
        ''')
        self.assertIsNone(facts["features"]["module_controls_observation"])
        self.assertFalse(facts["effect_summary_complete"])

    def test_loop_unknown_instead_of_silent_omission(self):
        with self.assertRaisesRegex(ValueError, "For"):
            self.facts('''
                if not removed:
                    for item in (x, 1):
                        if item:
                            return {"ok": False}
                return {"ok": True}
            ''')

    def test_label_injection_and_marker_misuse_rejected(self):
        text = source('if not removed:\n    scratch = x\nreturn {"ok": True}')
        with self.assertRaises(ValueError):
            extract_control(text, dict(DOMAINS, label=1))
        with self.assertRaises(ValueError):
            extract_control(text.replace("not removed", "removed or flag"), DOMAINS)

    def test_unknown_wrapper_is_rejected(self):
        text = source('if not removed:\n    scratch = x\nreturn {"ok": True}')
        with self.assertRaisesRegex(ValueError, "wrapper"):
            extract_control(text.replace('return {"error": str(error)}', 'return {"ok": True}'), DOMAINS)

    def test_local_rename_preserves_features(self):
        text = source('denied = False\nif not removed:\n    denied = x < 0\nif denied:\n    raise Rejected("NO")\nreturn {"ok": True}')
        class Rename(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == "denied":
                    node.id = "renamed"
                return node
        renamed = ast.unparse(Rename().visit(ast.parse(text)))
        self.assertEqual(extract_control(text, DOMAINS)["features"], extract_control(renamed, DOMAINS)["features"])


if __name__ == "__main__":
    unittest.main()
