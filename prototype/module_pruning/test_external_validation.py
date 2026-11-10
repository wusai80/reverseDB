import unittest

from external_validation import observe, summarize
from calibrate_risk import predict


class ValidationTest(unittest.TestCase):
    def test_exception_is_observation_not_discarded_execution(self):
        def execute(x, flag, removed):
            return 1 if removed else 1 // x
        before = observe(execute, {"x": 0, "flag": True}, False)
        self.assertEqual(before["type"], "ZeroDivisionError")
        self.assertNotEqual(before, observe(execute, {"x": 0, "flag": True}, True))

    def test_observation_snapshots_alias(self):
        cell = {"value": 0}
        before = observe(lambda *args: cell, {"x": 0, "flag": True}, False)
        cell["value"] = 1
        self.assertEqual(before["value"]["value"], 0)

    def test_outside_domain_does_not_require_or_invent_features(self):
        result = predict({"domain": "training"}, {}, "challenge")
        self.assertEqual(result["status"], "abstain")
        self.assertIsNone(result["probability"])

    def test_unknowns_and_cutoff_boundary_are_not_false_safe(self):
        def row(name, label, q):
            return dict(id=name, label=label, source_group="one", prediction=dict(
                diagnostic=None if q is None else dict(probability=q, source="joint_evidence_cell"),
                operational=dict(status="abstain"), deletion_authorized=False))
        report = summarize([row("bad",1,.4),row("boundary",0,.5),row("unknown",0,None)], .5)
        self.assertEqual(report["scorable"], 2)
        self.assertEqual(report["unsupported"], 1)
        self.assertEqual(report["diagnostic_classification"]["false_removal_ids"], ["bad"])
        self.assertEqual(report["diagnostic_classification"]["false_retention_ids"], ["boundary"])
        self.assertEqual(report["diagnostic_classification"]["removable_denominator"], 1)
        self.assertEqual(report["authorized_deletions"], 0)


if __name__ == "__main__": unittest.main()
