import copy
import json
from pathlib import Path
import unittest

from calibration_suite import build_suite,execute
from calibrate_risk import fit,predict,grouped_evaluation


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.protocol=json.loads(Path(__file__).with_name("calibration_protocol.json").read_text())
        self.rows=build_suite(self.protocol)

    def test_finite_labels_and_witnesses(self):
        self.assertEqual(len(self.rows),24)
        for row in self.rows:
            self.assertEqual(row["label"],int(row["variant"] in (0,3)))
            self.assertEqual(row["counterexample"] is not None,bool(row["label"]))
            if row["counterexample"]:
                state=row["counterexample"]["input"]
                self.assertNotEqual(execute(row,state,False),execute(row,state,True))

    def test_limited_success_does_not_define_label(self):
        self.assertTrue(all(r["features"]["limited_probe_pass"] for r in self.rows))
        self.assertEqual(sum(r["label"] for r in self.rows),12)

    def test_contract_domain_changes_label(self):
        strict=next(r for r in self.rows if r["id"]=="exception_control/0")
        normal=next(r for r in self.rows if r["id"]=="exception_control/1")
        self.assertEqual((strict["label"],normal["label"]),(1,0))
        self.assertEqual(len(normal["allowed_states"]),2)

    def test_entire_family_left_out(self):
        report=grouped_evaluation(self.rows,self.protocol,lambda f:.5)
        for fold in report["folds"]:
            self.assertNotIn(fold["held_out_family"],fold["model"]["training_families"])
            self.assertFalse(set(fold["test_ids"])&set(fold["model"]["training_ids"]))

    def test_holdout_labels_cannot_change_own_predictions(self):
        changed=copy.deepcopy(self.rows)
        for row in changed:
            if row["family"]=="heap_alias": row["label"]=1-row["label"]
        a=grouped_evaluation(self.rows,self.protocol,lambda f:.5)
        b=grouped_evaluation(changed,self.protocol,lambda f:.5)
        select=lambda r:[x["probability"] for x in r["forecasts"]["joint_calibrator"] if x["family"]=="heap_alias"]
        self.assertEqual(select(a),select(b))

    def test_duplicate_ids_and_unknown_labels_rejected(self):
        with self.assertRaises(ValueError): fit(self.rows+[self.rows[0]],self.protocol)
        changed=copy.deepcopy(self.rows); changed[0]["label"]=None
        with self.assertRaises(ValueError): fit(changed,self.protocol)

    def test_replicating_family_does_not_increase_weight(self):
        extra=[{**r,"id":r["id"]+"/copy"} for r in self.rows if r["family"]=="heap_alias"]
        a,b=fit(self.rows,self.protocol),fit(self.rows+extra,self.protocol)
        self.assertAlmostEqual(a["prior"],b["prior"])
        for key in a["cells"]: self.assertAlmostEqual(a["cells"][key]["probability"],b["cells"][key]["probability"])

    def test_real_domain_abstains(self):
        model=fit(self.rows,self.protocol)
        result=predict(model,self.rows[0]["features"],"real-orderstatus")
        self.assertIsNone(result["probability"])
        self.assertEqual(result["status"],"abstain")

    def test_unseen_evidence_falls_back_to_training_prior(self):
        model=fit(self.rows,self.protocol)
        result=predict(model,dict(discarded_result=True,limited_probe_pass=False),self.protocol["domain"])
        self.assertEqual(result["probability"],model["prior"])
        self.assertEqual(result["source"],"training_prevalence")

    def test_constant_probe_evidence_is_recorded(self):
        model=fit(self.rows,self.protocol)
        self.assertEqual(model["constant_features"],["limited_probe_pass"])

    def test_one_family_or_one_class_not_fittable(self):
        with self.assertRaises(ValueError): fit([r for r in self.rows if r["family"]=="heap_alias"],self.protocol)
        with self.assertRaises(ValueError): fit([r for r in self.rows if r["label"]==1],self.protocol)


if __name__=="__main__": unittest.main()
