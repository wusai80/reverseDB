import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("callsite_extraction", ROOT / "prototype/aggregate_reattach/callsite_extraction.py")
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


class CallsiteExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "sources/paperless-ngx/src/documents/views.py").read_text()
        cls.registry_source = (ROOT / "prototype/aggregate_reattach/tag_query_binding.py").read_text()
        cls.registry = extractor.extract_registry(cls.registry_source)
        cls.models = (ROOT / "sources/paperless-ngx/src/documents/models.py").read_text()
        cls.helper = (ROOT / "sources/paperless-ngx/src/documents/permissions.py").read_text()
        cls.audit = ROOT / "evidence/code-audit-fixes/20261104-13435f1b4859"
        cls.keys, issues = extractor.load_audited_attnames(cls.audit, cls.registry_source, cls.models, cls.helper)
        assert not issues, issues

    def custom(self, source):
        return next(c for c in extractor.extract_callsites(source)["candidates"] if c["class"] == "CustomFieldViewSet")

    def test_real_candidates_match_registry_without_admission(self):
        extracted = extractor.extract_callsites(self.source)
        self.assertEqual({c["class"] for c in extracted["candidates"]}, {"TagViewSet", "CustomFieldViewSet"})
        rows = extractor.compare_to_registry(extracted, self.registry, self.keys)
        self.assertTrue(all(r["status"] == "matches_registered_relation" for r in rows), rows)
        self.assertTrue(all(r["consumer_admission"] == "not_established" for r in rows))
        self.assertTrue(all(r["source_ordering"] != ["'pk'"] for r in rows))
        self.assertIn("CorrespondentViewSet", {s["class"] for s in extracted["skipped"]})

    def test_import_alias_is_resolved_without_importing_app(self):
        tree = ast.parse(self.source)
        class Rename(ast.NodeTransformer):
            def visit_ImportFrom(self, node):
                if node.module == "documents.models":
                    for a in node.names:
                        if a.name == "CustomFieldInstance": a.asname = "LinksAlias"
                return node
            def visit_Name(self, node):
                if node.id == "CustomFieldInstance": node.id = "LinksAlias"
                return node
        result = self.custom(ast.unparse(Rename().visit(tree)))
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["configuration"]["document_count_through"], "documents.models.CustomFieldInstance")

    def test_changed_relation_key_is_detected(self):
        changed = self.source.replace('document_count_source_field = "field_id"', 'document_count_source_field = "document_id"')
        rows = extractor.compare_to_registry(extractor.extract_callsites(changed), self.registry, self.keys)
        self.assertEqual(next(r for r in rows if r["class"] == "CustomFieldViewSet")["status"], "relation_key_mismatch")

    def test_dynamic_expressions_stay_unresolved(self):
        for before, after in (("model = CustomField", "model = unexecuted_model_factory()"),
                              ('document_count_source_field = "field_id"', 'document_count_source_field = unexecuted_field_factory()')):
            with self.subTest(before=before):
                candidate = self.custom(self.source.replace(before, after))
                self.assertEqual(candidate["status"], "needs_review")

    def test_duplicate_assignment_is_not_silently_overwritten(self):
        candidate = self.custom(self.source.replace("    model = CustomField\n", "    model = CustomField\n    model = Tag\n"))
        self.assertIn("missing_or_multiple:model", candidate["problems"])

    def test_local_method_override_requires_review(self):
        tree = ast.parse(self.source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CustomFieldViewSet")
        cls.body.append(ast.parse("def get_queryset(self):\n    return dynamic_query()\n").body[0])
        self.assertIn("local_override:get_queryset", self.custom(ast.unparse(tree))["problems"])

    def test_changed_mixin_wiring_requires_review(self):
        changed = self.source.replace("related_object_field=self._get_document_count_source_field(),",
                                      "related_object_field=self.document_count_related_name,")
        self.assertIn("ambiguous_or_unsupported_mixin_wiring", self.custom(changed)["problems"])

    def test_rebound_import_is_not_treated_as_original_model(self):
        candidate = self.custom(self.source + "\nCustomFieldInstance = another_symbol\n")
        self.assertIsNone(candidate["configuration"]["document_count_through"])

    def test_no_attname_inference_from_id_suffix(self):
        rows = extractor.compare_to_registry(extractor.extract_callsites(self.source), self.registry, {})
        self.assertTrue(all(r["status"] == "needs_audited_attname" for r in rows))

    def test_old_metadata_rejected_after_source_change(self):
        keys, issues = extractor.load_audited_attnames(
            ROOT / "evidence/aggregate-reuse/20261104-8db9a3e5b787",
            self.registry_source, self.models, self.helper)
        self.assertEqual(keys, {})
        self.assertIn("registry_revision_mismatch", issues)
        for registry, models, helper in ((self.registry_source + "\n# revised", self.models, self.helper),
                                          (self.registry_source, self.models + "\n# revised", self.helper)):
            with self.subTest(registry_changed=registry != self.registry_source):
                keys, issues = extractor.load_audited_attnames(self.audit, registry, models, helper)
                self.assertEqual(keys, {})
                self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()
