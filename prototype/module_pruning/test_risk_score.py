import copy
import json
import math
from pathlib import Path
import unittest

from risk_score import information_gain, posterior, rank_candidates, score_candidate


class RiskScoreTest(unittest.TestCase):
    def setUp(self):
        self.model=json.loads(Path(__file__).with_name("risk_model.json").read_text())
        self.candidate=dict(module="m",contract="C",gain_grid=[0.3,0.4,0.5])
        self.evidence=[dict(id="static",module="m",contract="C",kind="soft",group="static_result_use"),
                       dict(id="campaign",module="m",contract="C",kind="soft",group="differential_campaign")]

    def score(self,**changes):
        return score_candidate(changes.get("candidate",self.candidate),changes.get("evidence",self.evidence),changes.get("model",self.model))

    def test_analytic_posterior(self):
        self.assertAlmostEqual(self.score()["necessity_probability"]["central"],2/7)

    def test_empty_evidence_preserves_prior(self):
        p=self.score(evidence=[])["necessity_probability"]
        self.assertEqual(p["central"],0.5)
        for a,b in zip(p["sensitivity_envelope"],[.25,.75]): self.assertAlmostEqual(a,b)

    def test_duplicate_trials_do_not_accumulate(self):
        original=self.score()
        duplicate=self.score(evidence=self.evidence*180)
        self.assertEqual(original,duplicate)

    def test_same_campaign_different_ids_count_once(self):
        extra=[{**self.evidence[1],"id":f"trial-{i}"} for i in range(180)]
        a,b=self.score(),self.score(evidence=self.evidence+extra)
        self.assertEqual(a["necessity_probability"],b["necessity_probability"])
        self.assertTrue(all(g["update_count"]==1 for g in b["counted_groups"]))

    def test_conflicting_id_rejected(self):
        with self.assertRaises(ValueError): self.score(evidence=self.evidence+[{**self.evidence[0],"contract":"other"}])

    def test_other_contract_counterexample_ignored(self):
        r=self.score(evidence=self.evidence+[dict(id="fault",module="m",contract="fault-enabled",kind="counterexample")])
        self.assertFalse(r["hard_block"])
        self.assertEqual(r["necessity_probability"],self.score()["necessity_probability"])

    def test_counterexample_overrides_soft_scores(self):
        r=self.score(evidence=self.evidence+[dict(id="fault",module="m",contract="C",kind="counterexample")])
        self.assertEqual(r["necessity_probability"]["central"],1)
        self.assertEqual(r["block_reason"],"in_contract_counterexample")
        self.assertIsNone(r["next_validation"])

    def test_required_dependency_cannot_be_bought(self):
        r=self.score(candidate={**self.candidate,"gain_grid":[1e9]*3,"required_witnesses":["output -> module"]})
        self.assertTrue(r["hard_block"])
        self.assertFalse(r["ranking_eligible"])
        self.assertEqual(rank_candidates([r]),{})

    def test_missing_gain_not_zero(self):
        r=self.score(candidate={**self.candidate,"gain_grid":None})
        self.assertIsNone(r["utility"])
        self.assertFalse(r["ranking_eligible"])

    def test_negative_gain_kept(self):
        r=self.score(candidate={**self.candidate,"gain_grid":[-2,-1,0]})
        self.assertLess(r["utility"]["central"],0)

    def test_assumption_envelope(self):
        p=self.score()["necessity_probability"]
        self.assertAlmostEqual(p["sensitivity_envelope"][0],.04)
        self.assertAlmostEqual(p["sensitivity_envelope"][1],.75)
        self.assertEqual(p["scenario_count"],27)
        self.assertLessEqual(p["sensitivity_envelope"][0],p["central"])
        self.assertGreaterEqual(p["sensitivity_envelope"][1],p["central"])

    def test_risk_penalty_decreases_utility(self):
        model=copy.deepcopy(self.model); model["utility"]["risk_aversion"]*=2
        self.assertLess(self.score(model=model)["utility"]["central"],self.score()["utility"]["central"])

    def test_information_gain_known_cases(self):
        self.assertAlmostEqual(information_gain(.5,1,0),1)
        for q in [0,.04,.5,.75,1]:
            self.assertAlmostEqual(information_gain(q,0,0),0)
            self.assertAlmostEqual(information_gain(q,.3,.3),0)

    def test_budget_and_no_automatic_execution(self):
        model=copy.deepcopy(self.model); model["validation_budget"]=0
        r=self.score(model=model)
        self.assertIsNone(r["next_validation"])
        self.assertFalse(r["deletion_authorized"])

    def test_repeat_has_no_information_priority(self):
        r=self.score()
        repeat=next(p for p in r["validation_priority"] if p["id"]=="repeat_existing_inputs")
        self.assertEqual(repeat["sensitivity_envelope"],[0.0,0.0])
        self.assertNotEqual(r["next_validation"],"repeat_existing_inputs")

    def test_rank_is_within_contract_only(self):
        a=self.score(candidate={**self.candidate,"module":"a"},evidence=[])
        b=self.score(candidate={**self.candidate,"module":"b","gain_grid":[2,3,4]},evidence=[])
        c=self.score(candidate={**self.candidate,"module":"c","contract":"different"},evidence=[])
        self.assertEqual(rank_candidates([a,b,c]),{"C":["b","a"],"different":["c"]})

    def test_invalid_inputs_rejected(self):
        for value in [0,-1,float("nan"),float("inf"),True]:
            model=copy.deepcopy(self.model)
            model["groups"]["static_result_use"]["bayes_factor_grid"]=[value]*3
            with self.assertRaises(ValueError): self.score(model=model)
        with self.assertRaises(ValueError):
            self.score(evidence=[{**self.evidence[0],"group":"invented"}])

    def test_bayes_factor_monotonicity(self):
        for prior in [.01,.25,.5,.75,.99]:
            values=[posterior(prior,[factor]) for factor in [.1,.5,1,2,10]]
            self.assertEqual(values,sorted(values))
            self.assertAlmostEqual(values[2],prior)
        self.assertAlmostEqual(posterior(.5,[1e-300,1e300]),.5)


if __name__=="__main__":
    unittest.main()
