import copy
import unittest

from shared_risk import fit_shared, loss_derivatives, predict_shared


def fixture():
    protocol = dict(domain="unit-shared",old_features=["base"],control_features=["control"],alpha=1.,beta=1.)
    settings = {"lambda":1.,"optimizer":{"max_iterations":50,"gradient_tolerance":1e-10,"max_backtracks":25,"armijo":.0001}}
    rows = [dict(id=f"{g}/{v}",family=f"g{g}",domain=protocol["domain"],label=v,
                 features=dict(base=bool(v),control=False)) for g in range(3) for v in range(2)]
    return rows,protocol,settings


class SharedRiskTest(unittest.TestCase):
    def test_gradient_and_hessian_match_numerical_derivatives(self):
        beta = [.2,-.3]; x = [[-.5,.4],[.5,-.2],[.1,.3]]
        y = [0,1,1]; weights = [.5,.5,1.]; intercept = .15
        value,gradient,hessian = loss_derivatives(beta,x,y,weights,intercept,1.)
        self.assertGreater(value,0)
        epsilon = 1e-5
        for j in range(2):
            plus,minus = list(beta),list(beta)
            plus[j] += epsilon; minus[j] -= epsilon
            lp,gp,_ = loss_derivatives(plus,x,y,weights,intercept,1.)
            lm,gm,_ = loss_derivatives(minus,x,y,weights,intercept,1.)
            self.assertAlmostEqual((lp-lm)/(2*epsilon),gradient[j],places=8)
            for k in range(2):
                self.assertAlmostEqual((gp[k]-gm[k])/(2*epsilon),hessian[k][j],places=8)

    def test_unseen_combination_uses_shared_known_effect(self):
        rows,protocol,settings = fixture()
        model = fit_shared(rows,protocol,["base","control"],settings)
        result = predict_shared(model,dict(base=True,control=True),protocol["domain"])
        self.assertGreater(result["probability"],model["training_prior"])
        self.assertEqual(result["source"],"shared_additive_weights")
        self.assertEqual(result["training_unseen_values"],["control"])
        self.assertEqual(result["contributions"]["control"],0.)
        self.assertFalse(result["deletion_authorized"])

    def test_constant_features_reduce_exactly_to_training_prior(self):
        rows,protocol,settings = fixture()
        for row in rows:
            row["features"]["base"] = False
        rows[0]["label"] = 1
        model = fit_shared(rows,protocol,["base","control"],settings)
        result = predict_shared(model,dict(base=False,control=False),protocol["domain"])
        self.assertAlmostEqual(result["probability"],model["training_prior"],places=15)
        self.assertEqual(set(model["coefficients"].values()),{0.})

    def test_repeating_one_group_does_not_inflate_its_weight(self):
        rows,protocol,settings = fixture()
        model = fit_shared(rows,protocol,["base","control"],settings)
        duplicates = [dict(copy.deepcopy(row),id=row["id"]+"-copy") for row in rows if row["family"]=="g0"]
        repeated = fit_shared(rows+duplicates,protocol,["base","control"],settings)
        self.assertAlmostEqual(model["training_prior"],repeated["training_prior"],places=14)
        for name in model["features"]:
            self.assertAlmostEqual(model["coefficients"][name],repeated["coefficients"][name],places=12)

    def test_heldout_labels_do_not_enter_centers_or_fit(self):
        rows,protocol,settings = fixture()
        train = lambda rs:[r for r in rs if r["family"]!="g2"]
        model = fit_shared(train(rows),protocol,["base","control"],settings)
        for row in rows:
            if row["family"]=="g2":
                row["label"] = 1-row["label"]
        self.assertEqual(model,fit_shared(train(rows),protocol,["base","control"],settings))

    def test_unknown_and_external_domain_do_not_get_filled(self):
        rows,protocol,settings = fixture()
        model = fit_shared(rows,protocol,["base"],settings)
        result = predict_shared(model,dict(base=True,control=None),protocol["domain"])
        self.assertIsNone(result["probability"])
        self.assertEqual(result["reason"],"unknown_features")
        self.assertIsNone(predict_shared(model,{},"java-jdbc")["probability"])
        rows[0]["features"]["control"] = None
        with self.assertRaisesRegex(ValueError,"Complete"):
            fit_shared(rows,protocol,["base"],settings)

    def test_optimizer_is_deterministic_and_descends(self):
        rows,protocol,settings = fixture()
        a = fit_shared(rows,protocol,["base","control"],settings)
        b = fit_shared(rows,protocol,["base","control"],settings)
        self.assertEqual(a,b)
        objectives = [h["objective"] for h in a["optimizer_history"]]
        self.assertTrue(all(x>=y for x,y in zip(objectives,objectives[1:])))
        self.assertLessEqual(a["optimizer_history"][-1]["max_abs_gradient"],1e-10)

    def test_invalid_penalty_rejected(self):
        rows,protocol,settings = fixture()
        for penalty in [0,-1,True,float("nan"),float("inf")]:
            with self.assertRaises(ValueError):
                fit_shared(rows,protocol,["base"],dict(settings,**{"lambda":penalty}))


if __name__ == "__main__":
    unittest.main()
