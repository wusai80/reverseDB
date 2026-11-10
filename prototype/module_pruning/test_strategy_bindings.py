import unittest

from strategy_bindings import extract_bindings


SOURCE = '''
class Base:
    def __init__(self, sink, *, compact=False):
        self.sink = sink
        if compact:
            self.naming = self.short
        else:
            self.naming = self.long
    def short(self):
        return "short"
    def long(self):
        return "long"
class Exporter(Base):
    def emit(self):
        self.sink.write(self.naming())
'''
ROOTS=[{"class":"Exporter","method":"emit"}]


class StrategyBindingsTest(unittest.TestCase):
    def facts(self,source=SOURCE,domains=None,roots=None):
        return extract_bindings(source,ROOTS if roots is None else roots,{} if domains is None else domains)

    def test_explicit_domains_resolve_inherited_targets(self):
        for flag,target,other in [(True,"short","long"),(False,"long","short")]:
            facts=self.facts(domains={"compact":[flag]})
            self.assertEqual(facts["status"],"binding_facts_under_declared_scope")
            self.assertTrue(facts["candidate_reachability"]["Base."+target])
            self.assertFalse(facts["candidate_reachability"]["Base."+other])
            self.assertTrue(any(e["kind"]=="bound_slot_call" and e["target"]=="Base."+target for e in facts["edges"]))

    def test_unknown_option_does_not_infer_default(self):
        for source in [SOURCE,SOURCE.replace("compact=False","compact=True")]:
            facts=self.facts(source)
            self.assertEqual(set(facts["bindings"][0]["selected_definitions"]),{"Base.short","Base.long"})
            self.assertFalse(facts["bindings"][0]["default_used_to_narrow"])
            self.assertEqual(facts["bindings"][0]["context_origin"],"unknown_caller_option")

    def test_explicit_two_value_contract_keeps_both(self):
        facts=self.facts(domains={"compact":[True,False]})
        self.assertTrue(facts["candidate_reachability"]["Base.short"])
        self.assertTrue(facts["candidate_reachability"]["Base.long"])

    def test_constructor_lookup_exists_without_entry_calls(self):
        facts=self.facts(SOURCE.replace("self.sink.write(self.naming())","return None"),{"compact":[True]})
        self.assertTrue(facts["candidate_reachability"]["Base.short"])
        self.assertTrue(any(e["kind"]=="bind_time_lookup" for e in facts["edges"]))
        self.assertFalse(any(e["kind"]=="bound_slot_call" for e in facts["edges"]))

    def test_new_direct_consumer_restores_other_dependency(self):
        changed=SOURCE.replace("self.sink.write(self.naming())","self.short()\n        self.sink.write(self.naming())")
        facts=self.facts(changed,{"compact":[False]})
        self.assertTrue(facts["candidate_reachability"]["Base.short"])
        self.assertTrue(any(e["kind"]=="direct_self_call" for e in facts["edges"]))

    def test_subclass_override_resolves_concrete_implementation(self):
        changed=SOURCE+'\n    def short(self):\n        return "override"\n'
        facts=self.facts(changed,{"compact":[True]})
        self.assertTrue(facts["candidate_reachability"]["Exporter.short"])
        self.assertFalse(facts["candidate_reachability"]["Base.short"])

    def test_extra_entrypoint_is_included_only_when_declared(self):
        changed=SOURCE+'\n    def direct(self):\n        return self.short()\n'
        self.assertFalse(self.facts(changed,{"compact":[False]})["candidate_reachability"]["Base.short"])
        facts=self.facts(changed,{"compact":[False]},ROOTS+[{"class":"Exporter","method":"direct"}])
        self.assertTrue(facts["candidate_reachability"]["Base.short"])

    def test_constructor_selector_reassignment_is_unknown(self):
        changed=SOURCE.replace("if compact:","compact = False\n        if compact:")
        self.assertEqual(self.facts(changed,{"compact":[True]})["reason"],"selector_reassigned")

    def test_slot_overwrite_is_unknown(self):
        changed=SOURCE.replace("self.sink.write(self.naming())","self.naming = self.long\n        self.sink.write(self.naming())")
        self.assertEqual(self.facts(changed,{"compact":[True]})["reason"],"binding_slot_mutated")

    def test_method_shadowing_is_unknown(self):
        changed=SOURCE.replace("self.sink = sink","self.sink = sink\n        self.short = sink")
        self.assertEqual(self.facts(changed,{"compact":[True]})["reason"],"method_shadowed")

    def test_reflective_access_and_receiver_alias_are_unknown(self):
        for expression in ['getattr(self, "naming")()', 'alias = self\n        alias.naming()', 'self.__dict__["naming"]()']:
            facts=self.facts(SOURCE.replace("self.sink.write(self.naming())",expression))
            self.assertEqual(facts["status"],"unknown")
            self.assertIsNone(facts["candidate_reachability"])

    def test_bound_method_escape_is_unknown(self):
        changed=SOURCE.replace("self.sink.write(self.naming())","callback = self.naming\n        callback()")
        self.assertEqual(self.facts(changed)["reason"],"bound_method_escape")

    def test_multiple_inheritance_and_decorators_are_unknown(self):
        for source in [SOURCE.replace("Exporter(Base)","Exporter(Base, Other)"),
                       SOURCE.replace("    def short", "    @property\n    def short")]:
            self.assertEqual(self.facts(source)["status"],"unknown")

    def test_external_ancestor_is_unknown_even_with_local_methods(self):
        self.assertEqual(self.facts(SOURCE.replace("class Base:","class Base(Foreign):"))["reason"],"external_base_class")

    def test_inheritance_cycle_is_checked_before_local_lookup(self):
        self.assertEqual(self.facts(SOURCE.replace("class Base:","class Base(Base):"))["reason"],"inheritance_cycle")

    def test_reassigned_receiver_does_not_use_original_binding(self):
        changed=SOURCE.replace("self.sink.write(self.naming())","self = self.sink\n        self.naming()")
        self.assertEqual(self.facts(changed)["reason"],"receiver_reassigned")

    def test_module_patching_and_effectful_defaults_are_unknown(self):
        self.assertEqual(self.facts(SOURCE+'\npatch(Exporter)\n')["reason"],"module_initialization_outside_grammar")
        self.assertEqual(self.facts(SOURCE.replace("compact=False","compact=choose()"))["reason"],"effectful_default_argument")

    def test_names_are_not_hardcoded_to_paperless(self):
        renamed=SOURCE.replace("compact","variant").replace("naming","dispatch").replace("short","first").replace("long","second")
        facts=self.facts(renamed,{"variant":[True]})
        self.assertTrue(facts["candidate_reachability"]["Base.first"])
        self.assertFalse(facts["candidate_reachability"]["Base.second"])

    def test_label_and_non_boolean_parameter_injection_are_unknown(self):
        for context in [{"compact":[1]},{"compact":[False],"label":[True]}]:
            facts=self.facts(domains=context)
            self.assertEqual(facts["status"],"unknown")

    def test_external_effects_and_deletion_stay_unclosed(self):
        facts=self.facts(domains={"compact":[True]})
        self.assertIn("self.sink.write",[c["callee"] for c in facts["external_calls"]])
        self.assertFalse(facts["effect_summary_complete"])
        self.assertFalse(facts["deletion_authorized"])


if __name__=="__main__":
    unittest.main()
