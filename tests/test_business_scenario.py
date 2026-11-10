import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "business_scenario", Path(__file__).resolve().parents[1] / "prototype/business_scenario/simulate.py")
scenario = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario)


class BusinessScenarioTests(unittest.TestCase):
    def test_seed_reproducibility_and_referential_integrity(self):
        data = scenario.generate(42)
        self.assertEqual(data, scenario.generate(42))
        self.assertNotEqual(data["items"], scenario.generate(43)["items"])
        warehouses = {w["id"] for w in data["warehouses"]}
        districts = {d["id"] for d in data["districts"]}
        items = {i["id"] for i in data["items"]}
        self.assertTrue(all(c["district"] in districts and c["warehouse"] in warehouses for c in data["customers"]))
        self.assertTrue(all(s["warehouse"] in warehouses and s["item"] in items for s in data["stock"]))

    def test_duplicate_lines_cannot_overdraw_inventory(self):
        model = scenario.BusinessModel(scenario.generate())
        before = model.state()
        result = model.call("w1-sales", "new_order", customer="w1-d1-c1", lines=[(1, 60), (1, 60)])
        self.assertEqual(result["outcome"], "conflict")
        self.assertEqual(model.state(), before)

    def test_late_invalid_item_cannot_partially_reduce_stock(self):
        model = scenario.BusinessModel(scenario.generate())
        before = model.state()
        result = model.call("w1-sales", "new_order", customer="w1-d1-c1", lines=[(1, 10), (-1, 1)])
        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(model.state(), before)

    def test_other_warehouse_customer_cannot_be_used_to_create_order(self):
        model = scenario.BusinessModel(scenario.generate())
        before = model.state()
        result = model.call("w1-sales", "new_order", customer="w2-d1-c1", lines=[(1, 1)])
        self.assertEqual(result["outcome"], "not_found")
        self.assertEqual(model.state(), before)

    def test_two_seeds_complete_multi_actor_workflow(self):
        for seed in (42, 43):
            result = scenario.simulate(scenario.generate(seed))
            self.assertEqual(result["status"], "passed")
            contenders = result["events"][-2:]
            self.assertEqual({e["actor"] for e in contenders}, {"w1-sales", "w1-sales-second"})
            self.assertEqual(sorted(e["result"]["outcome"] for e in contenders), ["conflict", "ok"])
            self.assertFalse(result["application_observed"])


if __name__ == "__main__":
    unittest.main()
