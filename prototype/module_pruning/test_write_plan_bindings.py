import unittest

from write_plan_bindings import extract_write_plan


DTO='''
@dataclass
class Input:
    change: bool = False
    change_note: bool = False
    value: int = 0
    note: str = ""
'''
SOURCE='''
def helper(planned, removed, data, swap, item):
    if data.change:
        if item not in removed:
            item.amount = data.value
            planned.append(item)
    if data.change_note:
        if item not in removed:
            item.note = data.note
            if item not in planned:
                planned.append(item)

def caller(data, item, swap):
    planned = []
    removed = []
    helper(planned, removed, data, swap, item)
    if planned:
        persist(planned, ["amount", "note"])

def persist(lines, fields):
    Model.objects.bulk_update(lines, fields)
'''
BINDING=dict(helper="helper",dto_class="Input",dto_parameter="data",object_parameter="item",queue_parameter="planned",
             exclusion_parameter="removed",caller="caller",bulk_wrapper="persist")


class WritePlanTest(unittest.TestCase):
    def facts(self,source=SOURCE,domains=None,dto=DTO,binding=None):
        return extract_write_plan(source,dto,BINDING if binding is None else binding,{} if domains is None else domains)

    def test_guard_write_append_projection_chain(self):
        facts=self.facts(domains={"change":[True]})
        self.assertEqual(facts["status"],"local_write_facts_under_declared_scope")
        write=next(w for w in facts["writes"] if w["field"]=="amount")
        self.assertEqual(write["value"],"data.value")
        self.assertEqual(write["enqueue"]["kind"],"direct_append")
        self.assertTrue(write["projected_by_caller"] and write["may_flow_to_bulk_call"])
        self.assertEqual([g["expression"] for g in write["guards"]],["data.change","item not in removed"])

    def test_disabled_flags_narrow_but_defaults_do_not(self):
        unknown=self.facts()
        disabled=self.facts(domains={"change":[False],"change_note":[False]})
        self.assertTrue(all(w["may_reach"] for w in unknown["writes"]))
        self.assertFalse(any(w["may_reach"] for w in disabled["writes"]))
        self.assertFalse(any(g["default_used_to_narrow"] for g in unknown["guard_regions"]))

    def test_dedup_does_not_claim_same_object_on_skipped_branch(self):
        facts=self.facts()
        write=next(w for w in facts["writes"] if w["field"]=="note")
        self.assertEqual(write["enqueue"]["kind"],"append_if_not_member")
        self.assertTrue(write["enqueue"]["same_instance_on_append"])
        self.assertIsNone(write["enqueue"]["same_instance_if_append_skipped"])
        self.assertFalse(write["committed_effect_proven"])

    def test_equal_queue_member_can_be_a_different_instance(self):
        class Row:
            def __init__(self,pk,note): self.pk,self.note=pk,note
            def __eq__(self,other): return isinstance(other,Row) and self.pk==other.pk
        queued=Row(1,"old"); modified=Row(1,"new")
        plan=[queued]
        if modified not in plan: plan.append(modified)
        self.assertEqual(len(plan),1)
        self.assertIsNot(plan[0],modified)
        self.assertEqual(plan[0].note,"old")

    def test_removed_projection_breaks_field_path(self):
        facts=self.facts(SOURCE.replace('["amount", "note"]','["note"]'))
        amount=next(w for w in facts["writes"] if w["field"]=="amount")
        self.assertTrue(amount["may_reach"])
        self.assertFalse(amount["projected_by_caller"])
        self.assertFalse(amount["may_flow_to_bulk_call"])

    def test_missing_append_is_unknown_not_absent_effect(self):
        facts=self.facts(SOURCE.replace('            planned.append(item)\n','',1))
        amount=next(w for w in facts["writes"] if w["field"]=="amount")
        self.assertEqual(amount["enqueue"]["kind"],"unknown")
        self.assertIsNone(amount["may_flow_to_bulk_call"])

    def test_queue_clear_in_helper_is_unknown(self):
        facts=self.facts(SOURCE.replace('            planned.append(item)','            planned.clear()',1))
        self.assertEqual(facts["reason"],"unexpected_queue_mutation")

    def test_queue_mutation_or_rebinding_in_caller_keeps_local_facts_only(self):
        for inserted in ['planned.clear()','planned = []','alias = planned']:
            changed=SOURCE.replace('    if planned:',f'    {inserted}\n    if planned:')
            facts=self.facts(changed)
            self.assertEqual(facts["status"],"local_write_facts_under_declared_scope")
            self.assertEqual(facts["caller_link"]["status"],"unknown")
            self.assertTrue(all(w["may_flow_to_bulk_call"] is None for w in facts["writes"]))

    def test_chained_list_initialization_is_not_mistaken_for_unique_queue(self):
        changed=SOURCE.replace('    planned = []','    alias = planned = []')
        changed=changed.replace('    if planned:', '    alias.clear()\n    if planned:')
        facts=self.facts(changed)
        self.assertEqual(facts["caller_link"]["reason"],"queue_initializer_alias")
        self.assertTrue(all(w["may_flow_to_bulk_call"] is None for w in facts["writes"]))

    def test_wrapper_projection_mutation_is_unknown(self):
        changed=SOURCE.replace('    Model.objects.bulk_update','    fields.append("other")\n    Model.objects.bulk_update')
        self.assertEqual(self.facts(changed)["caller_link"]["reason"],"wrapper_input_escape_or_mutation")

    def test_DTO_write_and_receiver_alias_fail_closed(self):
        for line in ['data.change = False','alias = item','item = other']:
            changed=SOURCE.replace('    if data.change:',f'    {line}\n    if data.change:')
            self.assertEqual(self.facts(changed)["status"],"unknown")

    def test_aliased_caller_roles_are_unknown(self):
        changed=SOURCE.replace('helper(planned, removed, data, swap, item)\n','helper(planned, planned, data, swap, item)\n')
        self.assertEqual(self.facts(changed)["caller_link"]["reason"],"caller_roles_alias")

    def test_early_return_is_not_ignored(self):
        changed=SOURCE.replace('    if data.change:', '    return\n    if data.change:')
        self.assertEqual(self.facts(changed)["reason"],"unsupported_helper_control:Return")

    def test_property_DTO_and_label_injection_are_unknown(self):
        altered=DTO+'\n    @property\n    def hidden(self):\n        return True\n'
        self.assertEqual(self.facts(dto=altered)["reason"],"ordinary_dataclass_required")
        for context in [{"change":[1]},{"change":[True],"label":[False]}]:
            self.assertEqual(self.facts(domains=context)["status"],"unknown")

    def test_renamed_fields_are_derived_from_source(self):
        source=SOURCE.replace('change_note','edit_note').replace('amount','charge')
        dto=DTO.replace('change_note','edit_note')
        facts=self.facts(source,{"edit_note":[True]},dto)
        self.assertIn("charge",[w["field"] for w in facts["writes"]])
        self.assertIn("charge",facts["caller_link"]["projection_fields"])

    def test_unknown_call_effects_and_database_semantics_stay_open(self):
        changed=SOURCE.replace('    if data.change:', '    item.audit()\n    if data.change:')
        facts=self.facts(changed)
        self.assertIn("item.audit",[r["callee"] for r in facts["external_calls"]])
        self.assertFalse(facts["effect_summary_complete"])
        self.assertFalse(facts["caller_link"]["database_effect_summary_complete"])
        self.assertFalse(facts["deletion_authorized"])


if __name__=="__main__":
    unittest.main()
