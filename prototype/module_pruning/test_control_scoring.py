import copy
import unittest

from control_scoring import collisions, diagnostic_predict, evaluate, prepare


def fixture():
    rows = []
    groups = {}
    for group in range(3):
        ids = []
        for variant in range(2):
            ident = f"g{group}/{variant}"
            ids.append(ident)
            rows.append(dict(id=ident, label=variant, features=dict(base=bool(group%2), control=bool(variant))))
        groups[f"g{group}"] = ids
    protocol = dict(domain="unit-development", old_features=["base"], control_features=["control"],
                    groups=groups, alpha=1., beta=1., reliability_bin_edges=[0,.5,1.],
                    classification_cutoff=.5, source_groups=1)
    return rows, protocol


class ControlScoringTest(unittest.TestCase):
    def test_unknown_and_numeric_boolean_do_not_enter_cohort(self):
        rows, protocol = fixture()
        rows[0]["features"]["control"] = None
        rows[1]["features"]["control"] = 1
        rows[0]["feature_vector_complete"] = True
        included, excluded = prepare(rows, protocol)
        self.assertEqual(len(included),4)
        self.assertEqual([r["id"] for r in excluded], [rows[0]["id"],rows[1]["id"]])
        self.assertTrue(all(r["probability"] is None for r in excluded))

    def test_heldout_label_changes_do_not_change_that_fold_predictions(self):
        rows, protocol = fixture()
        first = evaluate(rows, protocol)
        changed = copy.deepcopy(rows)
        for row in changed[:2]:
            row["label"] = 1-row["label"]
        second = evaluate(changed, protocol)
        for name in ["old_eight","with_control","training_prior"]:
            a = [p["probability"] for p in first["forecasts"][name] if p["family"] == "g0"]
            b = [p["probability"] for p in second["forecasts"][name] if p["family"] == "g0"]
            self.assertEqual(a,b)
        self.assertEqual(first["folds"][0]["models"],second["folds"][0]["models"])

    def test_excluded_label_cannot_affect_fit(self):
        rows, protocol = fixture()
        rows[0]["features"]["control"] = None
        first = evaluate(rows, protocol)
        rows[0]["label"] = {"unresolved": True}
        second = evaluate(rows, protocol)
        self.assertEqual(first,second)

    def test_groups_stay_together_and_arms_share_training_ids(self):
        rows, protocol = fixture()
        result = evaluate(rows, protocol)
        for fold in result["folds"]:
            self.assertEqual(set(fold["test_ids"]),set(protocol["groups"][fold["held_out_group"]]))
            self.assertFalse(set(fold["test_ids"]) & set(fold["train_ids"]))
            for model in fold["models"].values():
                self.assertEqual(model["training_ids"],fold["train_ids"])

    def test_unknown_and_real_domain_abstain_without_training_prior(self):
        model = {"domain": "unit-development"}
        result = diagnostic_predict(model,{"control":None},"unit-development",["control"])
        self.assertEqual(result["reason"],"unknown_features")
        self.assertIsNone(result["probability"])
        result = diagnostic_predict(model,{"control":True},"java-jdbc",["control"])
        self.assertEqual(result["reason"],"outside_calibration_domain")
        self.assertFalse(result["deletion_authorized"])

    def test_guard_feature_separates_collision_without_claiming_accuracy(self):
        rows = [dict(id="a",label=0,features=dict(base=True,control=False)),
                dict(id="b",label=1,features=dict(base=True,control=True))]
        self.assertEqual(collisions(rows,["base"])["minimum_empirical_classification_errors"],1)
        self.assertEqual(collisions(rows,["base","control"])["minimum_empirical_classification_errors"],0)

    def test_duplicate_or_missing_group_members_fail(self):
        rows, protocol = fixture()
        protocol["groups"]["g1"].append("g0/0")
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            prepare(rows,protocol)
        rows, protocol = fixture()
        with self.assertRaisesRegex(ValueError,"membership"):
            prepare(rows[:-1],protocol)


if __name__ == "__main__":
    unittest.main()
