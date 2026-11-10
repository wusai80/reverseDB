"""Meaningful acceptance/rejection checks against the pinned upstream helper."""
import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "prototype/aggregate_reattach/transform_contract.py"
spec = importlib.util.spec_from_file_location("transform_contract", PATH)
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)
SOURCE = ROOT / "sources/paperless-ngx/src/documents/permissions.py"
HELPER = "annotate_document_count_by_ids"


class TransformContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.region = contract.recover_region(cls.source, HELPER)

    def complete_checks(self):
        # Deliberately synthetic evidence. Tests exercise the decision protocol,
        # not the validity of these claims for a live database or caller.
        return {k: contract.Evidence(v, "synthetic-test-evidence:" + k)
                for k, v in contract.REQUIREMENTS.items()}

    def test_recovers_symbolic_roles_from_real_source(self):
        self.assertEqual(self.region.roles["allowed_ids"], "document_ids")
        self.assertEqual(self.region.roles["group_key_parameter"], "related_object_field")
        self.assertTrue(self.region.roles["distinct"])
        self.assertNotIn("Tag", str(self.region.roles))

    def test_local_renaming_preserves_pattern(self):
        module = ast.parse(self.source)
        node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == HELPER)
        renames = {"counts": "grouped", "counts_by_pk": "lookup", "row": "record"}
        class Rename(ast.NodeTransformer):
            def visit_Name(self, n):
                n.id = renames.get(n.id, n.id)
                return n
        Rename().visit(node)
        recovered = contract.recover_region(ast.unparse(module), HELPER)
        self.assertEqual(recovered.roles["map_variable"], "lookup")

    def test_semantic_mutations_are_rejected(self):
        module = ast.parse(self.source)
        node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == HELPER)
        imports = "from django.db.models import Count, Value, IntegerField, Case, When\n"
        isolated = imports + ast.unparse(node)
        mutations = {
            "count_without_distinct": isolated.replace("distinct=True", "distinct=False"),
            "different_default": isolated.replace("default=Value(0)", "default=Value(1)"),
            "map_escapes": isolated.replace("if not counts_by_pk:", "publish(counts_by_pk)\n    if not counts_by_pk:"),
            "wrong_key": isolated.replace("When(pk=pk", "When(pk=count"),
        }
        for name, changed in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(changed, isolated)
                with self.assertRaises(contract.UnsupportedPattern):
                    contract.recover_region(changed, HELPER)

    def test_missing_context_never_emits_lowering(self):
        plan = contract.prepare_plan(self.region, self.region.source_sha256, {})
        self.assertEqual(plan["action"], "keep_original")
        self.assertIsNone(plan["lowering"])

    def test_missing_binding_is_unknown_not_a_proven_counterexample(self):
        plan = contract.prepare_plan(self.region, None, self.complete_checks())
        self.assertEqual(plan["decision"], "needs_evidence")
        self.assertIsNone(plan["lowering"])

    def test_declared_query_facts_do_not_count_as_verified(self):
        checks = self.complete_checks()
        checks["group_key_unique"] = contract.Evidence(contract.State.DECLARED, "caller guesses")
        self.assertEqual(contract.prepare_plan(self.region, self.region.source_sha256, checks)["decision"], "needs_evidence")

    def test_changed_source_invalidates_binding(self):
        self.assertEqual(contract.prepare_plan(self.region, "stale", self.complete_checks())["decision"], "rejected")

    def test_refuted_snapshot_prevents_candidate(self):
        checks = self.complete_checks()
        checks["fixed_read_state"] = contract.Evidence(contract.State.REFUTED, "concurrent writer")
        self.assertEqual(contract.prepare_plan(self.region, self.region.source_sha256, checks)["action"], "keep_original")

    def test_complete_protocol_prepares_but_does_not_execute(self):
        plan = contract.prepare_plan(self.region, self.region.source_sha256, self.complete_checks())
        self.assertEqual(plan["decision"], "eligible_for_lowering")
        self.assertEqual(plan["lowering"]["execution"], "not_implemented")

    def test_empty_references_and_misspelled_checks_fail_closed(self):
        checks = self.complete_checks()
        checks["entry_scope"] = contract.Evidence(contract.State.VERIFIED, "")
        self.assertEqual(contract.prepare_plan(self.region, self.region.source_sha256, checks)["action"], "keep_original")
        with self.assertRaises(ValueError):
            contract.prepare_plan(self.region, self.region.source_sha256, {"snapshot_typo": checks["entry_scope"]})


if __name__ == "__main__":
    unittest.main()
